"""Domain model for a financial account."""

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


class AccountType(str, Enum):
    """The kind of account, for display/grouping — never affects balance math.

    A fixed, closed set (same "controlled vocabulary via an Enum, not
    free text" convention as ``TransactionType``) rather than a
    free-text field, so the accounts list can group/label consistently
    instead of accumulating near-duplicate spellings ("Credit Card" vs.
    "credit card" vs. "CC").
    """

    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    CASH = "cash"
    INVESTMENT = "investment"
    OTHER = "other"


# Human-readable label per type, for the account form's <select> and the
# accounts table's Type column — AccountType.value alone (e.g.
# "credit_card") isn't presentable as-is, unlike TransactionType's values
# ("income"/"expense"/"transfer"), which already read fine capitalized.
ACCOUNT_TYPE_LABELS: dict[AccountType, str] = {
    AccountType.CHECKING: "Checking",
    AccountType.SAVINGS: "Savings",
    AccountType.CREDIT_CARD: "Credit Card",
    AccountType.CASH: "Cash",
    AccountType.INVESTMENT: "Investment",
    AccountType.OTHER: "Other",
}


class AccountStatus(str, Enum):
    """Whether an account is still in use — display-only, like ``AccountType``.

    Never affects balance math, transaction validation, or which
    accounts a transaction can reference — a closed account (one that's
    been emptied out, e.g. via a closing transfer) still keeps its full
    transaction history and can still be picked when recording a past
    transaction against it. This is purely "is this one still active
    day to day," surfaced so a long-closed account doesn't have to be
    deleted (deleting is blocked anyway while it has transactions — see
    ``services.accounts.remove_account``) just to stop cluttering the
    accounts list's mental model.
    """

    ACTIVE = "active"
    CLOSED = "closed"


ACCOUNT_STATUS_LABELS: dict[AccountStatus, str] = {
    AccountStatus.ACTIVE: "Active",
    AccountStatus.CLOSED: "Closed",
}


class Account(BaseModel):
    """A single account tracked by the ledger.

    ``id`` is a stable short code that never changes even if ``name`` or
    ``description`` does — transactions reference ``id``, never the name.
    ``account_type``/``status`` default to ``OTHER``/``ACTIVE`` so an
    ``accounts.toml`` written before these fields existed still reads
    back fine (same "missing key means the default" tolerance used
    elsewhere, e.g. ``Rule``'s amount bounds).
    """

    id: str
    name: str
    number: str
    description: str = ""
    starting_balance: Decimal
    account_type: AccountType = AccountType.OTHER
    status: AccountStatus = AccountStatus.ACTIVE
