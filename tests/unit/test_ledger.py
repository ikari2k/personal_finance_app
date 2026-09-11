"""Tests for app.storage.ledger read/write round-tripping."""

from datetime import date
from decimal import Decimal

from app.models.transaction import Transaction, TransactionType
from app.storage.ledger import read_ledger, write_ledger


def _transaction(**overrides) -> Transaction:
    fields = {
        "id": "t1",
        "date": date(2026, 1, 15),
        "account_id": "chk",
        "category": "Groceries",
        "subcategory": "Supermarket",
        "description": "Trader Joe's",
        "amount": Decimal("-42.17"),
        "type": TransactionType.EXPENSE,
        "transfer_id": None,
        "notes": None,
    }
    fields.update(overrides)
    return Transaction(**fields)


def test_read_ledger_returns_empty_list_when_file_missing(tmp_path):
    assert read_ledger(tmp_path / "ledger.csv") == []


def test_write_then_read_round_trips_a_transaction(tmp_path):
    path = tmp_path / "ledger.csv"
    transaction = _transaction()

    write_ledger([transaction], path)

    assert read_ledger(path) == [transaction]


def test_write_then_read_round_trips_a_transfer_pair(tmp_path):
    path = tmp_path / "ledger.csv"
    outflow = _transaction(
        id="t1",
        account_id="chk",
        amount=Decimal("-100.00"),
        type=TransactionType.TRANSFER,
        transfer_id="xfer1",
    )
    inflow = _transaction(
        id="t2",
        account_id="sav",
        amount=Decimal("100.00"),
        type=TransactionType.TRANSFER,
        transfer_id="xfer1",
    )

    write_ledger([outflow, inflow], path)

    assert read_ledger(path) == [outflow, inflow]


def test_write_then_read_round_trips_notes_and_blank_fields(tmp_path):
    path = tmp_path / "ledger.csv"
    transaction = _transaction(notes="reimbursed by Alex")

    write_ledger([transaction], path)

    assert read_ledger(path) == [transaction]
