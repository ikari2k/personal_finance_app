"""Domain model for a single ledger row."""

from datetime import date as date_
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


class TransactionType(str, Enum):
    """The kind of ledger row a transaction represents."""

    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"


class Transaction(BaseModel):
    """A single ledger row.

    A transfer between accounts is represented as two ``Transaction`` rows
    (one per account) sharing a ``transfer_id``, with opposite-sign
    ``amount`` values — never as a single row.
    """

    id: str
    date: date_
    account_id: str
    category: str
    subcategory: str
    description: str
    amount: Decimal
    type: TransactionType
    transfer_id: str | None = None
    notes: str | None = None
