"""Provider contract plus small helpers shared by the subprocess-based backends."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from localtts import lock
from localtts.errors import TTSError


class Provider:
    name = "base"
    #: Container format this backend writes. Used to name temp files.
    default_format = "wav"
    #: Whether this backend can act on `<tag>` tone tags (text.tone_segments()) itself.
    #: False for every backend without a real, verified tone/emotion hook -- text.
    #: synthesize_chunked() strips the tags before a provider that can't use them ever
    #: sees the literal brackets, rather than have it try to pronounce them.
    supports_tone_tags = False

    @property
    def max_words(self):
        """Words per synthesis call; 0 means the backend handles long text itself."""
        return int(self.settings.get("max_words") or 0)

    @property
    def max_workers(self):
        """How many chunks to synthesize concurrently when text.chunks() splits the input.

        Each chunk is a separate subprocess call paying its own fixed startup cost
        (process spawn, model load), so overlapping them is most of the win for a
        backend that has to chunk. Defaults to 1 (sequential, today's behavior)
        unless a provider sets its own default or the user overrides it.
        """
        return max(1, int(self.settings.get("max_workers") or 1))

    def __init__(self, settings, verbose=False, cfg=None):
        self.settings = settings
        self.verbose = verbose
        #: The full loaded configuration, not just this provider's own settings sub-dict.
        #: None unless the caller passed one via providers.build(). Only providers that
        #: compose another provider (rvc, chaining a base TTS backend) need this; most
        #: never touch it.
        self.cfg = cfg

    def synthesize(self, text, out_path, voice=None):
        raise NotImplementedError

    def check(self):
        """Return (ok, message) describing whether this backend can run."""
        return True, "ready"

    # -- helpers -------------------------------------------------------

    def get(self, key, default=None):
        value = self.settings.get(key, default)
        return default if value in (None, "") and default is not None else value

    def path(self, key):
        raw = self.settings.get(key) or ""
        return os.path.expanduser(raw) if raw else ""

    def resolve_binary(self, key="binary", fallback=""):
        name = os.path.expanduser(self.settings.get(key) or fallback)
        found = shutil.which(name) or (name if os.path.isfile(name) and os.access(name, os.X_OK) else None)
        if not found:
            raise TTSError(
                "%s: %r not found on PATH. Install it, or point at it with "
                "`tts config --set %s.%s=/path/to/%s`." % (self.name, name, self.name, key, name)
            )
        return found

    def run(self, cmd, stdin_text=None, cwd=None):
        if self.verbose:
            print("+ %s%s" % ("cd %s && " % cwd if cwd else "", " ".join(cmd)), file=sys.stderr)
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_text,
                text=True,
                cwd=cwd,
                stdout=None if self.verbose else subprocess.PIPE,
                stderr=None if self.verbose else subprocess.PIPE,
            )
        except FileNotFoundError:
            raise TTSError("%s: command not found: %s" % (self.name, cmd[0]))
        except PermissionError:
            raise TTSError("%s: not executable: %s" % (self.name, cmd[0]))
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            tail = "\n".join(detail.splitlines()[-15:])
            raise TTSError(
                "%s failed (exit %d)%s" % (os.path.basename(cmd[0]), proc.returncode, "\n" + tail if tail else "")
            )
        return proc

    # -- optional persistent-server client -------------------------------------
    #
    # A backend that reloads a heavy model (an onnx graph, a torch checkpoint) on every
    # single call pays that cost per call. A provider can offer a "talk to a server that
    # keeps the model loaded" mode instead: this tool never runs that server itself --
    # it's a plain script the local-tts-configure skill writes into the backend's own
    # venv (the same pattern as its subprocess CLI wrapper) -- but any provider can reuse
    # this client-side plumbing to reach one and auto-start it if it isn't already up.

    def server_alive(self, url, health_path="/health"):
        try:
            with urllib.request.urlopen(url.rstrip("/") + health_path, timeout=2) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def ensure_server(self, url, start_command, timeout, health_path="/health"):
        """If `url` isn't already answering `health_path`, launch `start_command` in the
        background and poll until it does, or raise after `timeout` seconds. A model
        load can genuinely take that long on first start, so this is meant to be patient,
        not a quick liveness probe.

        Multiple local-tts processes (separate agent sessions, a chunked synthesis
        run's concurrent workers) can hit this for the same server at once. Without
        coordination, every one of them would see it down and spawn a duplicate --
        racing for the same port. A single lock file per server URL, held with a real
        OS-level advisory lock (flock/msvcrt.locking rather than a "file exists"
        marker), serializes that: whoever gets the lock first is the one that actually
        starts it, and it's released automatically if that process dies for any reason,
        so there's no stale-lock cleanup to worry about. Everyone else blocks on the
        lock, then re-checks health once they get it -- by then the first one has
        usually already finished starting it, so they just proceed.
        """
        if self.server_alive(url, health_path):
            return

        lock_path = os.path.join(
            tempfile.gettempdir(),
            "local-tts-server-%s.lock" % hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
        )
        with open(lock_path, "a+") as lock_handle:
            lock.acquire(lock_handle)
            try:
                if self.server_alive(url, health_path):   # someone else started it while we waited
                    return
                self._start_and_wait(url, start_command, timeout, health_path)
            finally:
                lock.release(lock_handle)

    def _start_and_wait(self, url, start_command, timeout, health_path):
        if not start_command:
            raise TTSError(
                "%s: nothing answering %s, and no server_start command configured. "
                "Either start the server yourself, or set "
                "`tts config --set %s.server_start=...` (see the local-tts-configure skill)."
                % (self.name, url, self.name)
            )
        try:
            cmd = shlex.split(start_command)
        except ValueError as exc:
            raise TTSError("%s.server_start is not valid shell syntax: %s" % (self.name, exc))
        if self.verbose:
            print("+ %s &" % " ".join(cmd), file=sys.stderr)
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                start_new_session=(sys.platform != "win32"),
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise TTSError("%s: could not start the server (%r): %s" % (self.name, start_command, exc))

        deadline = time.time() + max(1.0, float(timeout))
        while time.time() < deadline:
            if self.server_alive(url, health_path):
                return
            time.sleep(0.5)
        raise TTSError(
            "%s: started %r but nothing answered %s within %ss -- check it can actually "
            "run (missing model files, wrong port, a crash on startup)."
            % (self.name, start_command, url, timeout)
        )

    def post_for_audio(self, url, path, payload, timeout=120):
        """POST JSON to `url` + `path`, return the raw audio bytes from the response body."""
        request = urllib.request.Request(
            url.rstrip("/") + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                audio = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace").strip()
            raise TTSError("%s server error (HTTP %s) from %s%s: %s"
                           % (self.name, exc.code, url, path, body[:500]))
        except urllib.error.URLError as exc:
            raise TTSError("%s: could not reach %s%s: %s" % (self.name, url, path, exc.reason))
        if not audio:
            raise TTSError("%s server returned an empty response" % self.name)
        return audio
