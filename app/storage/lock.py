"""PID-based file locking with automatic stale-lock recovery.

The app is the sole writer of the ledger and config files, but a write
should still be guarded against being run twice at once (e.g. two
concurrent requests). A lock file next to the target, containing the
writer's PID, provides that guard; if the process that created a lock file
is no longer running (e.g. it crashed), the lock is treated as stale and
cleared automatically — no manual cleanup is ever required.
"""

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LockError(RuntimeError):
    """Raised when a lock file is held by another live process."""


def _pid_is_alive(pid: int) -> bool:
    """Return whether a process with the given PID is currently running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but is owned by another user — still alive.
        return True
    else:
        return True


def _clear_stale_lock(lock_path: Path) -> None:
    """Remove ``lock_path`` if it names a PID that is no longer running."""
    try:
        pid_text = lock_path.read_text().strip()
        pid = int(pid_text)
    except (OSError, ValueError):
        # Missing, empty, or unreadable lock file — nothing to recover from.
        lock_path.unlink(missing_ok=True)
        return
    if not _pid_is_alive(pid):
        lock_path.unlink(missing_ok=True)


@contextmanager
def file_lock(
    target: Path, timeout: float = 5.0, poll_interval: float = 0.05
) -> Iterator[None]:
    """Acquire an exclusive, PID-based lock on ``target`` for the block body.

    A stale lock (one whose owning PID is no longer alive) is cleared
    automatically before each acquisition attempt. Raises ``LockError`` if
    the lock is still held by a live process after ``timeout`` seconds.
    """
    lock_path = target.with_name(target.name + ".lock")
    deadline = time.monotonic() + timeout
    while True:
        _clear_stale_lock(lock_path)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise LockError(f"Timed out waiting for lock on {target}") from None
            time.sleep(poll_interval)
            continue
        with os.fdopen(fd, "w") as lock_file:
            lock_file.write(str(os.getpid()))
        break
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
