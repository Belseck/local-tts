"""Audio playback using whatever command-line player the system already has."""

import os
import shutil
import subprocess
import sys

from localtts.errors import TTSError

# (executable, argv builder) in preference order.
PLAYERS = [
    ("ffplay", lambda p: ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", p]),
    ("paplay", lambda p: ["paplay", p]),
    ("aplay", lambda p: ["aplay", "-q", p]),
    ("afplay", lambda p: ["afplay", p]),          # macOS
    ("play", lambda p: ["play", "-q", p]),        # sox
    ("mpv", lambda p: ["mpv", "--no-video", "--really-quiet", p]),
    ("cvlc", lambda p: ["cvlc", "--play-and-exit", "--intf", "dummy", p]),
]


def _is_wsl():
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="ignore") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def _powershell_command(path):
    """On WSL, hand the file to Windows' own player."""
    exe = shutil.which("powershell.exe")
    if not exe:
        return None
    win_path = path
    if shutil.which("wslpath"):
        try:
            win_path = subprocess.check_output(["wslpath", "-w", os.path.abspath(path)], text=True).strip()
        except subprocess.CalledProcessError:
            return None
    script = "(New-Object Media.SoundPlayer '%s').PlaySync()" % win_path.replace("'", "''")
    return [exe, "-NoProfile", "-NonInteractive", "-Command", script]


def find_player(path, preferred=""):
    if preferred:
        exe = shutil.which(preferred)
        if not exe:
            raise TTSError("configured player %r was not found on PATH" % preferred)
        for name, build in PLAYERS:
            if name == os.path.basename(preferred):
                return build(path)
        return [exe, path]

    for name, build in PLAYERS:
        if shutil.which(name):
            return build(path)

    if _is_wsl():
        return _powershell_command(path)
    return None


def play(path, preferred="", verbose=False):
    """Play a file. Returns True if something played, False if no player exists."""
    cmd = find_player(path, preferred)
    if not cmd:
        return False
    if verbose:
        print("+ %s" % " ".join(cmd), file=sys.stderr)
    stream = None if verbose else subprocess.DEVNULL
    try:
        subprocess.run(cmd, check=True, stdout=stream, stderr=stream)
    except subprocess.CalledProcessError as exc:
        raise TTSError("playback failed (%s exited with %d)" % (cmd[0], exc.returncode))
    except KeyboardInterrupt:
        return True
    return True


def available_players():
    found = [name for name, _ in PLAYERS if shutil.which(name)]
    if not found and _is_wsl() and shutil.which("powershell.exe"):
        found.append("powershell.exe")
    return found
