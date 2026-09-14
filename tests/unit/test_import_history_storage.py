"""Tests for app.storage.import_history read/write round-tripping."""

from datetime import datetime

from app.models.import_history import ImportHistoryEntry
from app.storage.import_history import append_history_entry, read_history, write_history


def _entry(**overrides) -> ImportHistoryEntry:
    fields = {
        "timestamp": datetime(2026, 9, 14, 10, 0, 0),
        "bank": "Credit Agricole",
        "account_id": "chk",
        "account_name": "Everyday Checking",
        "new_count": 132,
        "duplicate_count": 0,
        "filtered_count": 2,
    }
    fields.update(overrides)
    return ImportHistoryEntry(**fields)


def test_read_history_returns_empty_list_when_file_missing(tmp_path):
    assert read_history(tmp_path / "import_history.toml") == []


def test_write_then_read_round_trips_history(tmp_path):
    path = tmp_path / "import_history.toml"
    entries = [_entry(bank="A"), _entry(bank="B")]

    write_history(entries, path)

    assert {e.bank for e in read_history(path)} == {"A", "B"}


def test_read_history_sorts_newest_first(tmp_path):
    path = tmp_path / "import_history.toml"
    older = _entry(bank="Older", timestamp=datetime(2026, 1, 1))
    newer = _entry(bank="Newer", timestamp=datetime(2026, 9, 1))
    write_history([older, newer], path)

    result = read_history(path)

    assert [e.bank for e in result] == ["Newer", "Older"]


def test_append_history_entry_adds_to_existing(tmp_path):
    path = tmp_path / "import_history.toml"
    write_history([_entry(bank="First")], path)

    append_history_entry(_entry(bank="Second"), path)

    assert {e.bank for e in read_history(path)} == {"First", "Second"}
