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

    ``counterparty_account`` is a raw, normalized (whitespace-stripped)
    bank account number captured at CSV-import time when the bank's
    export names the other party to the transaction (see
    ``app.models.import_mapping``'s ``counterparty_account``/
    ``counterparty_account_fallback`` columns) — blank for manual entries
    and for imports whose mapping doesn't capture it. It doesn't mean
    this row *is* a transfer (import-time rows are still always income or
    expense — see ``app.services.importer``'s module docstring); it's
    only ever consulted afterward, by ``app.services.transactions
    .find_transfer_matches``, to spot rows whose counterparty is actually
    one of the user's own registered accounts.
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
    counterparty_account: str = ""
