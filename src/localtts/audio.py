"""Audio playback using whatever command-line player the system already has."""

import errno
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave

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


def _powershell_exe():
    """PowerShell, whether we are on Windows itself or reaching out of WSL."""
    for name in ("powershell.exe", "pwsh.exe", "powershell", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _powershell_command(path):
    """Play through Windows' own sound player (native Windows, or WSL reaching out)."""
    exe = _powershell_exe()
    if not exe:
        return None
    win_path = os.path.abspath(path)
    if _is_wsl() and shutil.which("wslpath"):
        try:
            win_path = subprocess.check_output(
                ["wslpath", "-w", os.path.abspath(path)], text=True).strip()
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

    # On Windows, PowerShell's player is always there; prefer it over hunting for ffplay.
    if sys.platform == "win32":
        return _powershell_command(path) or _first_player(path)

    found = _first_player(path)
    if found:
        return found
    if _is_wsl():
        return _powershell_command(path)
    return None


def _first_player(path):
    for name, build in PLAYERS:
        if shutil.which(name):
            return build(path)
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
    if sys.platform == "win32" or (not found and _is_wsl()):
        exe = _powershell_exe()
        if exe:
            found.insert(0, os.path.basename(exe))
    return found


def concat_wavs(paths, out_path, gap_seconds=0.35):
    """Join same-format wav files into one, with a short silence between them."""
    if not paths:
        raise TTSError("nothing to join: every chunk failed")
    if len(paths) == 1 and paths[0] == out_path:
        return out_path

    with wave.open(paths[0], "rb") as first:
        params = first.getparams()
    silence = b"\x00" * int(params.framerate * gap_seconds) * params.sampwidth * params.nchannels

    with wave.open(out_path, "wb") as out:
        out.setparams(params)
        for index, path in enumerate(paths):
            with wave.open(path, "rb") as part:
                if part.getparams()[:3] != params[:3]:
                    raise TTSError("cannot join %s: format differs from the first chunk" % path)
                out.writeframes(part.readframes(part.getnframes()))
            if index != len(paths) - 1:
                out.writeframes(silence)
    return out_path


def duration(path):
    with wave.open(path, "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


# --- detached playback -------------------------------------------------
#
# Playing in the background means the caller (usually an agent) is not blocked for the
# length of the audio. The player's pid is recorded in a small state file so a later
# `stop` / `pause` / `resume` can find it.

STATE_FILE = os.path.join(tempfile.gettempdir(), "local-tts-playback.json")


def _write_state(pid, path, duration_seconds=0.0, paused=False, elapsed=0.0, segment_start=None):
    """`elapsed` is time already spent playing before the current running segment (0 if
    never paused); `segment_start` is when the current running segment began, or None
    while paused. Together they let playback_status() compute progress without polling
    the player process for position, which most players do not expose anyway."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump({
                "pid": pid,
                "path": os.path.abspath(path),
                "duration": duration_seconds,
                "paused": paused,
                "elapsed": elapsed,
                "segment_start": segment_start,
            }, handle)
    except OSError:
        pass          # a lost state file only costs us the stop/pause convenience


def read_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and state.get("pid") else None


def clear_state():
    try:
        os.unlink(STATE_FILE)
    except OSError:
        pass


def is_running(pid):
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                             capture_output=True, text=True)
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def play_detached(path, preferred="", verbose=False):
    """Start playback in the background. Returns (pid, duration_seconds), or (None, 0)."""
    cmd = find_player(path, preferred)
    if not cmd:
        return None, 0.0
    if verbose:
        print("+ %s &" % " ".join(cmd), file=sys.stderr)

    stop_previous()          # one playback at a time, so stop/pause stay unambiguous
    stream = None if verbose else subprocess.DEVNULL
    kwargs = {"stdout": stream, "stderr": stream, "stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(cmd, **kwargs)
    except OSError as exc:
        raise TTSError("could not start %s: %s" % (cmd[0], exc))
    try:
        length = duration(path)
    except (wave.Error, EOFError, OSError):
        length = 0.0
    _write_state(process.pid, path, duration_seconds=length, segment_start=time.time())
    return process.pid, length


def _elapsed(state):
    base = float(state.get("elapsed") or 0.0)
    segment_start = state.get("segment_start")
    if segment_start is None:
        return base
    return base + max(0.0, time.time() - float(segment_start))


def _signal_playback(sig, name, paused):
    """Send a signal to the recorded player. Returns (ok, message)."""
    state = read_state()
    if not state:
        return False, "nothing is playing"
    pid = int(state["pid"])
    if not is_running(pid):
        clear_state()
        return False, "nothing is playing (the last playback already finished)"
    if sig is None:
        return False, ("%s is not supported on this platform; use `stop` instead" % name)
    try:
        os.kill(pid, sig)
    except OSError as exc:
        return False, "could not %s pid %d: %s" % (name, pid, exc)

    elapsed = _elapsed(state)
    _write_state(pid, state.get("path") or "", duration_seconds=float(state.get("duration") or 0.0),
                paused=paused, elapsed=elapsed, segment_start=None if paused else time.time())
    return True, "%s: %s" % (name, state.get("path") or pid)


def stop_previous():
    """Stop any playback we started earlier, quietly."""
    state = read_state()
    if state and is_running(int(state["pid"])):
        stop_playback()
    else:
        clear_state()


def stop_playback():
    state = read_state()
    if not state:
        return False, "nothing is playing"
    pid = int(state["pid"])
    if not is_running(pid):
        clear_state()
        return False, "nothing is playing (the last playback already finished)"
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, check=False)
    else:
        # Resume first: a paused process never sees SIGTERM.
        cont = getattr(signal, "SIGCONT", None)
        if cont is not None:
            try:
                os.kill(pid, cont)
            except OSError:
                pass
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            return False, "could not stop pid %d: %s" % (pid, exc)
    path = state.get("path")
    clear_state()
    return True, "stopped: %s" % (path or pid)


def pause_playback():
    return _signal_playback(getattr(signal, "SIGSTOP", None), "paused", paused=True)


def resume_playback():
    return _signal_playback(getattr(signal, "SIGCONT", None), "resumed", paused=False)


def format_time(seconds):
    seconds = max(0, int(seconds))
    return "%d:%02d" % (seconds // 60, seconds % 60)


def progress_bar(elapsed, total, width=20):
    """A static text bar: '[####------] 0:04 / 0:12'. No terminal control codes —
    safe to print once and leave in a transcript, unlike a carriage-return spinner."""
    if total <= 0:
        return "[%s] %s" % ("?" * width, format_time(elapsed))
    filled = min(width, int(width * min(1.0, elapsed / total)))
    bar = "#" * filled + "-" * (width - filled)
    return "[%s] %s / %s" % (bar, format_time(elapsed), format_time(total))


def playback_status():
    state = read_state()
    if not state:
        return False, "nothing is playing"
    pid = int(state["pid"])
    if not is_running(pid):
        clear_state()
        return False, "nothing is playing"
    label = "paused" if state.get("paused") else "playing"
    elapsed = _elapsed(state)
    total = float(state.get("duration") or 0.0)
    bar = progress_bar(elapsed, total)
    return True, "%s %s (pid %d): %s" % (label, bar, pid, state.get("path") or "?")
