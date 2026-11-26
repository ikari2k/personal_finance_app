"""Payee logo files under ``data/payee_logos/``.

One image per payee, named by ``services.payee_logos.logo_stem`` plus its type's
extension. The directory listing is cached against the directory's mtime (and
dropped on every write here), since the Transactions list asks once per row.
"""

import os
from pathlib import Path
from urllib.parse import quote

from app.services.payee_logos import LOGO_FILENAME, logo_stem, sniff_image
from app.storage.lock import file_lock

# directory -> (directory mtime, {stem: (filename, file mtime)})
_cache: dict[Path, tuple[int, dict[str, tuple[str, int]]]] = {}


def _dir(directory: Path | None) -> Path:
    """Resolve the logo directory at call time so tests can monkeypatch config."""
    from app import config

    return directory if directory is not None else config.PAYEE_LOGOS_DIR


def _listing(directory: Path | None) -> dict[str, tuple[str, int]]:
    """Return ``{stem: (filename, mtime)}`` for the logos on disk."""
    folder = _dir(directory)
    try:
        stamp = folder.stat().st_mtime_ns
    except FileNotFoundError:
        return {}
    cached = _cache.get(folder)
    if cached and cached[0] == stamp:
        return cached[1]
    found: dict[str, tuple[str, int]] = {}
    with os.scandir(folder) as entries:
        for entry in entries:
            if LOGO_FILENAME.match(entry.name):
                found[entry.name.split(".")[0]] = (
                    entry.name,
                    entry.stat().st_mtime_ns,
                )
    _cache[folder] = (stamp, found)
    return found


def logo_url(name: str, directory: Path | None = None) -> str:
    """Return the URL of payee ``name``'s logo, or ``""`` if it has none."""
    if not name:
        return ""
    hit = _listing(directory).get(logo_stem(name))
    if not hit:
        return ""
    return f"/payees/logo/{quote(hit[0])}?v={hit[1]}"


def logo_path(filename: str, directory: Path | None = None) -> Path | None:
    """Return the file for ``filename`` if it is a well-formed logo name that exists."""
    if not LOGO_FILENAME.match(filename):
        return None
    path = _dir(directory) / filename
    return path if path.is_file() else None


def _remove(stem: str, folder: Path) -> bool:
    """Delete every file stored for ``stem``; whether there was one."""
    removed = False
    for ext in ("png", "jpg", "gif", "webp", "svg"):
        path = folder / f"{stem}.{ext}"
        if path.exists():
            path.unlink()
            removed = True
    return removed


def save_logo(name: str, data: bytes, directory: Path | None = None) -> None:
    """Store ``data`` as payee ``name``'s logo, replacing any it had.

    Raises ``ValueError`` if ``data`` is not an acceptable image.
    """
    ext = sniff_image(data)
    folder = _dir(directory)
    folder.mkdir(parents=True, exist_ok=True)
    stem = logo_stem(name)
    with file_lock(folder / "logos"):
        _remove(stem, folder)
        tmp = folder / f"{stem}.{ext}.tmp"
        tmp.write_bytes(data)
        tmp.replace(folder / f"{stem}.{ext}")
    _cache.pop(folder, None)


def delete_logo(name: str, directory: Path | None = None) -> bool:
    """Remove payee ``name``'s logo; whether it had one."""
    folder = _dir(directory)
    if not folder.exists():
        return False
    with file_lock(folder / "logos"):
        removed = _remove(logo_stem(name), folder)
    _cache.pop(folder, None)
    return removed


def carry_logos(sources: list[str], target: str, directory: Path | None = None) -> None:
    """Move a logo onto ``target`` after ``sources`` were renamed or merged into it.

    ``target`` keeps its own logo if it has one, otherwise it takes the first
    source's; the sources' files are then removed. A rename that only changes
    the case shares one file and is left alone.
    """
    folder = _dir(directory)
    if not folder.exists():
        return
    target_stem = logo_stem(target)
    with file_lock(folder / "logos"):
        listing = _listing(directory)
        have = target_stem in listing
        for source in sources:
            stem = logo_stem(source)
            if stem == target_stem or stem not in listing:
                continue
            filename = listing[stem][0]
            if not have:
                ext = filename.split(".")[1]
                (folder / filename).replace(folder / f"{target_stem}.{ext}")
                have = True
            else:
                (folder / filename).unlink(missing_ok=True)
    _cache.pop(folder, None)
