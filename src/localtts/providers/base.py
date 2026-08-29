"""Provider contract plus small helpers shared by the subprocess-based backends."""

import os
import shutil
import subprocess
import sys

from localtts.errors import TTSError


class Provider:
    name = "base"
    #: Container format this backend writes. Used to name temp files.
    default_format = "wav"

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
