"""Payee logo rules: how a payee's logo file is named, and what counts as an image.

Pure functions — no file I/O (``app.storage.payee_logos`` does that). A logo is
stored under a name derived from its payee's name, so there is no index to keep
in step with the ledger; renaming or merging a payee just moves the file.
"""

import hashlib
import re
import unicodedata

MAX_LOGO_BYTES = 512 * 1024
LOGO_FILENAME = re.compile(r"^[0-9a-f]{16}\.(png|jpg|gif|webp|svg)$")


def logo_stem(name: str) -> str:
    """Return the file stem for payee ``name`` (case-insensitive, 16 hex digits)."""
    key = unicodedata.normalize("NFC", name.strip()).casefold()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def sniff_image(data: bytes) -> str:
    """Return the file extension for image bytes, judged by content, not by name.

    Accepts PNG, JPEG, GIF, WebP and SVG (without scripts). Raises ``ValueError``
    with a message for the user if the file is empty, too big or not one of those.
    """
    if not data:
        raise ValueError("That file is empty.")
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError(f"That image is over {MAX_LOGO_BYTES // 1024} KB.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    text = data[:200_000].decode("utf-8", errors="ignore").lower()
    if "<svg" in text:
        if "<script" in text or "javascript:" in text:
            raise ValueError("SVG logos with scripts are not allowed.")
        return "svg"
    raise ValueError("Use a PNG, JPEG, GIF, WebP or SVG image.")
