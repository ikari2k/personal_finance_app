"""Tests for app.storage.backup and its use by the config writers."""

from datetime import date, timedelta

from app.models.rule import Rule
from app.storage.backup import KEEP_DAYS, backup_before_write
from app.storage.categories import read_categories, write_categories
from app.storage.rules import write_rules


def test_no_backup_when_file_missing(tmp_path):
    backup_before_write(tmp_path / "x.toml")

    assert list(tmp_path.iterdir()) == []


def test_bak_tracks_previous_version_but_daily_snapshot_is_never_overwritten(
    tmp_path,
):
    path = tmp_path / "x.toml"
    day = date(2026, 10, 8)
    path.write_text("good")

    backup_before_write(path, day)  # about to replace "good"
    path.write_text("bad")
    backup_before_write(path, day)  # about to replace "bad", same day

    assert (tmp_path / "x.toml.bak").read_text() == "bad"
    assert (tmp_path / "backups" / "x.toml.2026-10-08").read_text() == "good"


def test_new_day_gets_its_own_snapshot(tmp_path):
    path = tmp_path / "x.toml"
    path.write_text("a")
    backup_before_write(path, date(2026, 10, 8))
    path.write_text("b")

    backup_before_write(path, date(2026, 10, 9))

    assert (tmp_path / "backups" / "x.toml.2026-10-09").read_text() == "b"


def test_old_snapshots_are_pruned(tmp_path):
    path = tmp_path / "x.toml"
    path.write_text("a")
    start = date(2026, 1, 1)
    for offset in range(KEEP_DAYS + 5):
        backup_before_write(path, start + timedelta(days=offset))

    snapshots = sorted((tmp_path / "backups").glob("x.toml.*"))

    assert len(snapshots) == KEEP_DAYS
    assert snapshots[0].name == "x.toml.2026-01-06"


def test_write_categories_keeps_the_previous_file(tmp_path):
    path = tmp_path / "categories.toml"
    tagged = {
        "income": {},
        "expense": {
            "Food": {"icon": "", "budget": "", "bucket": "need", "subcategories": {}}
        },
    }
    write_categories(tagged, path)

    write_categories({"income": {}, "expense": {}}, path)  # a "bad" overwrite

    restored = read_categories(tmp_path / "categories.toml.bak")
    assert restored["expense"]["Food"]["bucket"] == "need"
    assert list((tmp_path / "backups").glob("categories.toml.*"))


def test_write_rules_creates_backup_on_overwrite(tmp_path):
    path = tmp_path / "rules.toml"
    rule = Rule(pattern="x", field="description", category="A", subcategory="")
    write_rules([rule], path)

    write_rules([], path)

    assert (tmp_path / "rules.toml.bak").exists()
