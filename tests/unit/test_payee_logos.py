"""Tests for payee logo rules and storage."""

import pytest

from app.services.payee_logos import MAX_LOGO_BYTES, logo_stem, sniff_image
from app.storage.payee_logos import (
    carry_logos,
    delete_logo,
    logo_path,
    logo_url,
    save_logo,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="1"/></svg>'


def test_stem_ignores_case_and_spacing_around_the_name():
    assert logo_stem(" Żabka ") == logo_stem("żabka")
    assert logo_stem("Lidl") != logo_stem("Biedronka")


@pytest.mark.parametrize(
    ("data", "ext"),
    [
        (PNG, "png"),
        (b"\xff\xd8\xff\xe0" + b"0" * 8, "jpg"),
        (b"GIF89a" + b"0" * 8, "gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
        (SVG, "svg"),
    ],
)
def test_sniff_recognises_images_by_content(data, ext):
    assert sniff_image(data) == ext


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"just text",
        b"<html><script>x</script></html>",
        b"<svg><script>alert(1)</script></svg>",
        b'<svg><a href="javascript:x"/></svg>',
        PNG + b"0" * MAX_LOGO_BYTES,
    ],
)
def test_sniff_refuses_everything_else(data):
    with pytest.raises(ValueError):
        sniff_image(data)


def test_save_lookup_replace_and_delete(tmp_path):
    assert logo_url("Lidl", tmp_path) == ""
    save_logo("Lidl", PNG, tmp_path)
    first = logo_url("lidl", tmp_path)
    assert first.startswith("/payees/logo/") and first.endswith(
        f"?v={first.split('=')[1]}"
    )
    save_logo("Lidl", SVG, tmp_path)  # a different type replaces, never adds
    assert len(list(tmp_path.glob("*.*"))) == 1
    assert ".svg" in logo_url("Lidl", tmp_path)
    assert delete_logo("Lidl", tmp_path) is True
    assert logo_url("Lidl", tmp_path) == ""
    assert delete_logo("Lidl", tmp_path) is False


def test_logo_path_only_resolves_well_formed_names(tmp_path):
    save_logo("Lidl", PNG, tmp_path)
    name = next(tmp_path.glob("*.png")).name
    assert logo_path(name, tmp_path) is not None
    assert logo_path("../etc/passwd", tmp_path) is None
    assert logo_path("logos.lock", tmp_path) is None
    assert logo_path("0123456789abcdef.png", tmp_path) is None


def test_carry_moves_the_first_logo_and_keeps_the_targets_own(tmp_path):
    save_logo("Lidl 1", PNG, tmp_path)
    save_logo("Lidl 2", SVG, tmp_path)
    carry_logos(["Lidl 1", "Lidl 2"], "Lidl", tmp_path)
    assert logo_url("Lidl 1", tmp_path) == logo_url("Lidl 2", tmp_path) == ""
    assert ".png" in logo_url("Lidl", tmp_path)

    save_logo("Shop", SVG, tmp_path)
    save_logo("Shop B", PNG, tmp_path)
    carry_logos(["Shop B"], "Shop", tmp_path)
    assert ".svg" in logo_url("Shop", tmp_path)
    assert logo_url("Shop B", tmp_path) == ""


def test_carry_leaves_a_case_only_rename_alone(tmp_path):
    save_logo("lidl", PNG, tmp_path)
    carry_logos(["lidl"], "Lidl", tmp_path)
    assert ".png" in logo_url("Lidl", tmp_path)
