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


def test_write_then_read_round_trips_counterparty_account(tmp_path):
    path = tmp_path / "ledger.csv"
    transaction = _transaction(counterparty_account="85194010182000")

    write_ledger([transaction], path)

    assert read_ledger(path) == [transaction]


def test_read_ledger_tolerates_a_file_written_before_counterparty_account_existed(
    tmp_path,
):
    path = tmp_path / "ledger.csv"
    path.write_text(
        "id,date,account_id,category,subcategory,description,amount,type,"
        "transfer_id,notes\n"
        "t1,2026-01-15,chk,Groceries,Supermarket,Trader Joe's,-42.17,expense,,\n"
    )

    [transaction] = read_ledger(path)

    assert transaction.counterparty_account == ""


def test_write_then_read_round_trips_payee(tmp_path):
    path = tmp_path / "ledger.csv"
    transaction = _transaction(payee="Cinema City")

    write_ledger([transaction], path)

    assert read_ledger(path) == [transaction]


def test_read_ledger_tolerates_a_file_written_before_payee_existed(tmp_path):
    path = tmp_path / "ledger.csv"
    path.write_text(
        "id,date,account_id,category,subcategory,description,amount,type,"
        "transfer_id,notes,counterparty_account\n"
        "t1,2026-01-15,chk,Groceries,Supermarket,Trader Joe's,-42.17,expense,,,\n"
    )

    [transaction] = read_ledger(path)

    assert transaction.payee == ""


def test_write_then_read_round_trips_source(tmp_path):
    path = tmp_path / "ledger.csv"
    transaction = _transaction(source="cred")

    write_ledger([transaction], path)

    assert read_ledger(path) == [transaction]


def test_read_ledger_tolerates_a_file_written_before_source_existed(tmp_path):
    path = tmp_path / "ledger.csv"
    path.write_text(
        "id,date,account_id,category,subcategory,description,amount,type,"
        "transfer_id,notes,counterparty_account,payee\n"
        "t1,2026-01-15,chk,Groceries,Supermarket,Trader Joe's,-42.17,expense,,,,\n"
    )

    [transaction] = read_ledger(path)

    assert transaction.source == ""


def test_write_then_read_round_trips_payee_fields_by_label(tmp_path):
    path = tmp_path / "ledger.csv"
    transaction = _transaction(
        payee_fields={"Web address": "cinema-city.pl", "Recipient": "Cinema City SA"}
    )

    write_ledger([transaction], path)

    assert read_ledger(path) == [transaction]
    assert read_ledger(path)[0].payee_fields["Web address"] == "cinema-city.pl"


def test_rows_without_payee_fields_write_a_blank_cell(tmp_path):
    path = tmp_path / "ledger.csv"

    write_ledger([_transaction()], path)

    assert read_ledger(path)[0].payee_fields == {}
    assert path.read_text().splitlines()[1].endswith(",")  # blank last cell


def test_unreadable_payee_fields_cell_reads_as_no_fields(tmp_path):
    path = tmp_path / "ledger.csv"
    path.write_text(
        "id,date,account_id,category,subcategory,description,amount,type,"
        "transfer_id,notes,counterparty_account,payee,source,payee_fields\n"
        't1,2026-01-15,chk,Food,,x,-1.00,expense,,,,,,"{not json"\n'
        't2,2026-01-15,chk,Food,,x,-1.00,expense,,,,,,"[1, 2]"\n'
    )

    assert [t.payee_fields for t in read_ledger(path)] == [{}, {}]


def test_a_ledger_from_before_payee_fields_existed_reads_with_none(tmp_path):
    path = tmp_path / "ledger.csv"
    path.write_text(
        "id,date,account_id,category,subcategory,description,amount,type,"
        "transfer_id,notes,counterparty_account,payee,source\n"
        "t1,2026-01-15,chk,Food,,x,-1.00,expense,,,,,cred\n"
    )

    [transaction] = read_ledger(path)

    assert transaction.payee_fields == {} and transaction.source == "cred"
