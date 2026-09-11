"""Tests for app.storage.accounts read/write round-tripping."""

from decimal import Decimal

from app.models.account import Account
from app.storage.accounts import read_accounts, write_accounts


def test_read_accounts_returns_empty_list_when_file_missing(tmp_path):
    assert read_accounts(tmp_path / "accounts.toml") == []


def test_write_then_read_round_trips_accounts(tmp_path):
    path = tmp_path / "accounts.toml"
    accounts = [
        Account(
            id="chk",
            name="Checking",
            number="1234",
            description="Main checking account",
            starting_balance=Decimal("1000.00"),
        ),
        Account(
            id="sav",
            name="Savings",
            number="5678",
            description="",
            starting_balance=Decimal("-50.5"),
        ),
    ]

    write_accounts(accounts, path)

    assert read_accounts(path) == accounts


def test_starting_balance_precision_survives_round_trip(tmp_path):
    path = tmp_path / "accounts.toml"
    account = Account(
        id="chk",
        name="Checking",
        number="1",
        description="",
        starting_balance=Decimal("1234.567"),
    )

    write_accounts([account], path)

    assert read_accounts(path)[0].starting_balance == Decimal("1234.567")
