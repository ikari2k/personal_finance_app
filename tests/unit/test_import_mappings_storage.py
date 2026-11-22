"""Tests for app.storage.import_mappings read/write round-tripping."""

import pytest

from app.models.import_mapping import ImportMapping, PayeeFieldColumn
from app.storage.import_mappings import (
    delete_mapping,
    list_all_mappings,
    read_mapping,
    write_mapping,
)


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


def test_list_all_mappings_returns_full_objects_sorted(tmp_path):
    write_mapping(_mapping("Credit Agricole"), tmp_path)
    write_mapping(_mapping("Chase"), tmp_path)

    result = list_all_mappings(tmp_path)

    assert [m.bank for m in result] == ["Chase", "Credit Agricole"]
    assert result[0] == _mapping("Chase")


def test_delete_mapping_removes_the_file(tmp_path):
    write_mapping(_mapping("Credit Agricole"), tmp_path)

    delete_mapping("Credit Agricole", tmp_path)

    assert read_mapping("Credit Agricole", tmp_path) is None


def test_delete_mapping_is_a_noop_when_nothing_to_delete(tmp_path):
    delete_mapping("Ghost Bank", tmp_path)  # must not raise


def test_payee_field_columns_round_trip_in_order_with_their_labels(tmp_path):
    mapping = _mapping("Credit Agricole").model_copy(
        update={
            "payee_fields": [
                PayeeFieldColumn(label="Web address", column=89),
                PayeeFieldColumn(label="Recipient", column=10),
            ]
        }
    )

    write_mapping(mapping, tmp_path)

    assert read_mapping("Credit Agricole", tmp_path) == mapping


def test_a_mapping_saved_before_payee_fields_existed_reads_with_none(tmp_path):
    (tmp_path / "old.toml").write_text(
        'bank = "old"\n[columns]\ndate = 0\ndescription = 1\namount = 2\n'
    )

    assert read_mapping("old", tmp_path).payee_fields == []


def test_a_mapping_allows_at_most_five_payee_fields():
    five = [PayeeFieldColumn(label=f"F{i}", column=i) for i in range(5)]
    six = [PayeeFieldColumn(label=f"F{i}", column=i) for i in range(6)]

    assert (
        len(
            ImportMapping(bank="x", columns={"date": 0}, payee_fields=five).payee_fields
        )
        == 5
    )
    with pytest.raises(ValueError):
        ImportMapping(bank="x", columns={"date": 0}, payee_fields=six)
