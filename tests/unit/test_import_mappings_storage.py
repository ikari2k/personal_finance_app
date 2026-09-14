"""Tests for app.storage.import_mappings read/write round-tripping."""

import pytest

from app.models.import_mapping import ImportMapping
from app.storage.import_mappings import list_banks, read_mapping, write_mapping


def _mapping(bank: str) -> ImportMapping:
    return ImportMapping(
        bank=bank,
        delimiter=";",
        encoding="cp1250",
        date_format="%d.%m.%Y",
        decimal_separator=",",
        columns={"date": 69, "description": 23, "amount": 81, "account_number": 67},
    )


def test_read_mapping_returns_none_when_missing(tmp_path):
    assert read_mapping("Credit Agricole", tmp_path) is None


def test_write_then_read_round_trips_mapping(tmp_path):
    mapping = _mapping("Credit Agricole")

    write_mapping(mapping, tmp_path)

    assert read_mapping("Credit Agricole", tmp_path) == mapping


def test_bank_name_lookup_is_slug_insensitive_to_case_and_spacing(tmp_path):
    write_mapping(_mapping("Credit Agricole"), tmp_path)

    assert read_mapping("  credit AGRICOLE  ", tmp_path) is not None


def test_write_mapping_rejects_blank_bank_name(tmp_path):
    with pytest.raises(ValueError):
        write_mapping(_mapping("   "), tmp_path)


def test_list_banks_returns_display_names_sorted(tmp_path):
    write_mapping(_mapping("Credit Agricole"), tmp_path)
    write_mapping(_mapping("Chase"), tmp_path)

    assert list_banks(tmp_path) == ["Chase", "Credit Agricole"]


def test_list_banks_empty_when_directory_missing(tmp_path):
    assert list_banks(tmp_path / "does-not-exist") == []
