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

    def __init__(self, settings, verbose=False):
        self.settings = settings
        self.verbose = verbose

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

    def run(self, cmd, stdin_text=None):
        if self.verbose:
            print("+ %s" % " ".join(cmd), file=sys.stderr)
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_text,
                text=True,
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
