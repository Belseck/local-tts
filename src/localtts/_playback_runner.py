"""Internal worker spawned by audio.play_detached() -- not a public entry point.

Detached playback must not block the calling `tts` process, but the audio itself
still has to wait its turn if something else (any session, any provider) is
already playing. So play_detached() doesn't launch the player directly: it
launches this script, which blocks *itself* on the machine-wide playback lock
first, marks the state file as actually playing once it gets its turn, then
runs the real player to completion before releasing the lock. The caller sees
none of that waiting.

Invoked as: python -m localtts._playback_runner <lock_path> <session-or-empty> <player argv...>
"""

import os
import subprocess
import sys
import time

from localtts import audio, lock


def main(argv):
    lock_path, session = argv[0], (argv[1] or None)
    player_cmd = argv[2:]
    with open(lock_path, "a+") as handle:
        lock.acquire(handle)
        try:
            state = audio.read_state(session)
            if state and int(state.get("pid") or -1) == os.getpid():
                audio._write_state(
                    os.getpid(), state.get("path") or "",
                    duration_seconds=float(state.get("duration") or 0.0),
                    paused=False, elapsed=0.0, segment_start=time.time(), session=session,
                )
            subprocess.run(player_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL)
        finally:
            lock.release(handle)


if __name__ == "__main__":
    main(sys.argv[1:])
