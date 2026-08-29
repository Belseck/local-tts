"""A small cross-platform advisory file lock.

Shared by anything that needs to serialize across separate OS processes --
persistent-server auto-start, audio playback -- without a stale-lock cleanup
problem: the lock is tied to the open file descriptor, so it's released
automatically if the holder dies for any reason, crash included.
"""

import sys

if sys.platform == "win32":
    import msvcrt

    def acquire(handle):
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)   # blocks until acquired

    def release(handle):
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def acquire(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)   # blocks until acquired

    def release(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
