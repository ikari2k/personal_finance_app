"""Tests for app.storage.accounts read/write round-tripping."""

from decimal import Decimal

from app.models.account import Account, AccountStatus, AccountType
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


def test_account_type_round_trips(tmp_path):
    path = tmp_path / "accounts.toml"
    account = Account(
        id="cc",
        name="Rewards Card",
        number="9012",
        description="",
        starting_balance=Decimal("-320.15"),
        account_type=AccountType.CREDIT_CARD,
    )

    write_accounts([account], path)

    assert read_accounts(path)[0].account_type is AccountType.CREDIT_CARD


def test_reading_an_account_written_before_account_type_existed_defaults_to_other(
    tmp_path,
):
    path = tmp_path / "accounts.toml"
    path.write_text(
        "[[accounts]]\n"
        'id = "chk"\n'
        'name = "Checking"\n'
        'number = "1"\n'
        'description = ""\n'
        'starting_balance = "100.00"\n'
    )

    [account] = read_accounts(path)

    assert account.account_type is AccountType.OTHER


def test_account_status_round_trips(tmp_path):
    path = tmp_path / "accounts.toml"
    account = Account(
        id="old",
        name="Old Account",
        number="1234",
        description="",
        starting_balance=Decimal("0"),
        status=AccountStatus.CLOSED,
    )

    write_accounts([account], path)

    assert read_accounts(path)[0].status is AccountStatus.CLOSED


def test_reading_an_account_written_before_status_existed_defaults_to_active(
    tmp_path,
):
    path = tmp_path / "accounts.toml"
    path.write_text(
        "[[accounts]]\n"
        'id = "chk"\n'
        'name = "Checking"\n'
        'number = "1"\n'
        'description = ""\n'
        'starting_balance = "100.00"\n'
    )

    [account] = read_accounts(path)

    assert account.status is AccountStatus.ACTIVE
