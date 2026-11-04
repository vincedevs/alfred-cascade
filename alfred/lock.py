"""Only one Alfred at a time. Two would fight over the microphone and the database (say,
the Pi's service and a manual `uv run alfred`), and a reset must not pull the files out
from under a running one. The lock is released when the process ends."""

import fcntl
from pathlib import Path


def only_one_alfred(data_dir: Path):
    """Take the lock, or None if another Alfred holds it. Keep the result for as long as
    the lock should be held."""
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = open(data_dir / "alfred.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock
