"""Keeps app.models.category's icon key data internally consistent."""

from app.models.category import ICON_HINTS, VALID_ICONS


def test_icon_hints_cover_every_valid_icon_key_exactly():
    assert set(ICON_HINTS) == VALID_ICONS
