"""Safety copies of a config file, taken just before the app overwrites it.

The app is the sole writer of its files and keeps no history, so a bug (or a
mistaken edit) that rewrites a file wrongly would otherwise be permanent.
Two copies are made from the file's state *before* each write:

* ``<name>.bak`` next to the file — the previous version, replaced on every
  write (cheap undo of the last change).
* ``backups/<name>.<YYYY-MM-DD>`` in the same directory — the state before
  the *first* write of each day, kept for ``KEEP_DAYS`` days. Unlike the
  ``.bak``, this survives a bad write being followed by more writes the same
  day, because it is never overwritten once made.

Backups are best-effort: a failure to copy never blocks the real write.
"""

import logging
import shutil
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

KEEP_DAYS = 30
BACKUP_DIR_NAME = "backups"


def backup_before_write(path: Path, today: date | None = None) -> None:
    """Copy ``path`` aside (as ``.bak`` and, once a day, a dated snapshot).

    Does nothing if ``path`` doesn't exist yet. Call this inside the file's
    lock, before replacing it. ``today`` is injectable for tests.
    """
    if not path.exists():
        return
    day = today or date.today()
    try:
        shutil.copy2(path, path.with_name(path.name + ".bak"))
        backup_dir = path.parent / BACKUP_DIR_NAME
        backup_dir.mkdir(exist_ok=True)
        snapshot = backup_dir / f"{path.name}.{day.isoformat()}"
        if not snapshot.exists():
            shutil.copy2(path, snapshot)
        _prune(backup_dir, path.name)
    except OSError:
        logger.warning("Could not back up %s before writing", path, exc_info=True)


def _prune(backup_dir: Path, name: str) -> None:
    """Delete all but the newest ``KEEP_DAYS`` dated snapshots of ``name``."""
    snapshots = sorted(backup_dir.glob(f"{name}.????-??-??"))
    for old in snapshots[:-KEEP_DAYS]:
        old.unlink(missing_ok=True)
