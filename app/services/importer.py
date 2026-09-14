"""Parse a bank CSV export into candidate ledger transactions.

Pure functions over in-memory data — no file I/O, matching every other
``services`` module (the router reads the uploaded file's bytes and any
existing ledger/mapping/rules via ``storage`` and passes them in here).

Imported rows are always income or expense, never transfers: pairing one
imported row with another as a linked transfer isn't attempted — a bank
export has no reliable signal for "this outflow and that inflow are the
same transfer" beyond amount/date proximity, which is too fragile to
guess at automatically. A transfer between the user's own accounts will
simply import as two independent rows.
"""

import csv
import io
import re
import uuid
from dataclasses import dataclass
from datetime import date as date_
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.models.import_mapping import ImportMapping
from app.models.rule import Rule
from app.models.transaction import Transaction, TransactionType
from app.services.categorizer import categorize

DEFAULT_CATEGORY = "Uncategorized"


@dataclass
class ParsedRow:
    """One parsed-but-not-yet-committed CSV row."""

    date: date_
    description: str
    amount: Decimal
    account_number: str = ""


def parse_amount(raw: str, decimal_separator: str) -> Decimal:
    """Parse a raw amount cell like ``"-5,99 PLN"`` into a ``Decimal``.

    Strips everything except digits, ``-``, and ``decimal_separator``
    before parsing — this handles a currency-code suffix and any
    thousands-separator character generically (they're simply not in the
    allowed set), then normalizes ``decimal_separator`` to ``.`` if it
    isn't already. Raises ``ValueError`` if what's left doesn't parse.
    """
    allowed = re.escape(decimal_separator)
    cleaned = re.sub(rf"[^0-9\-{allowed}]", "", raw)
    if decimal_separator != ".":
        cleaned = cleaned.replace(decimal_separator, ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount '{raw}'") from exc


def parse_date(raw: str, date_format: str) -> date_:
    """Parse a raw date cell using ``date_format``; raises ``ValueError`` on failure."""
    try:
        return datetime.strptime(raw.strip(), date_format).date()
    except ValueError as exc:
        raise ValueError(f"invalid date '{raw}' for format '{date_format}'") from exc


def _normalize_account_number(value: str) -> str:
    """Strip all whitespace and case so account numbers compare reliably.

    Bank exports format IBANs with spacing that may not match how the
    user typed ``Account.number`` into this app (see
    ``app.models.import_mapping.ImportMapping``'s ``account_number``
    column note).
    """
    return re.sub(r"\s+", "", value).upper()


def parse_rows(csv_text: str, mapping: ImportMapping) -> list[ParsedRow]:
    """Parse ``csv_text`` into ``ParsedRow``s using ``mapping``.

    The first row is always treated as a header and skipped. Blank rows,
    rows with a blank date or amount cell, and rows whose amount parses
    to exactly zero (not a real transaction) are silently skipped.
    """
    rows = list(csv.reader(io.StringIO(csv_text), delimiter=mapping.delimiter))
    if not rows:
        return []
    date_idx = mapping.columns["date"]
    description_idx = mapping.columns["description"]
    amount_idx = mapping.columns["amount"]
    account_idx = mapping.columns.get("account_number")

    parsed: list[ParsedRow] = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        if not row[date_idx].strip() or not row[amount_idx].strip():
            # Some transaction types in a real export legitimately leave
            # the mapped date or amount column blank for that row (e.g. an
            # account-fee row with no operation date, or a domestic
            # transaction leaving a foreign-currency amount column empty)
            # — skip the row rather than failing the whole import over one
            # sparse field, the same treatment already given to a
            # fully-blank row above. A non-blank value that still doesn't
            # parse (wrong date_format, an actually-malformed amount)
            # keeps failing loudly below — that's a genuine mapping
            # mistake, not a sparse field.
            continue
        amount = parse_amount(row[amount_idx], mapping.decimal_separator)
        if amount == 0:
            continue
        parsed.append(
            ParsedRow(
                date=parse_date(row[date_idx], mapping.date_format),
                description=row[description_idx].strip(),
                amount=amount,
                account_number=row[account_idx].strip()
                if account_idx is not None
                else "",
            )
        )
    return parsed


def count_blank_column_values(
    csv_text: str, delimiter: str, column_index: int
) -> tuple[int, int]:
    """Return ``(blank_count, total_data_rows)`` for one column across the file.

    Fully-blank rows aren't counted as data rows (matching ``parse_rows``,
    which ignores them too). Used to warn when a candidate date/amount
    column is blank for a large fraction of rows — those rows get
    silently skipped by ``parse_rows``, which is worth surfacing
    explicitly rather than just producing a suspiciously small "N new
    transactions" count with no explanation (a wrong column choice, e.g.
    a foreign-currency amount column that's empty for every domestic
    transaction, is the most common cause).
    """
    rows = list(csv.reader(io.StringIO(csv_text), delimiter=delimiter))
    if not rows:
        return (0, 0)
    data_rows = [row for row in rows[1:] if any(cell.strip() for cell in row)]
    blank = sum(
        1
        for row in data_rows
        if column_index >= len(row) or not row[column_index].strip()
    )
    return (blank, len(data_rows))


def filter_by_account_number(
    rows: list[ParsedRow], account_number: str
) -> tuple[list[ParsedRow], int]:
    """Keep only rows matching ``account_number``; return ``(kept, skipped_count)``.

    A row with no account-number value (mapping has no such column, or
    that cell was blank) is always kept — there's nothing to filter it
    against, so excluding it would silently drop data rather than
    filtering it deliberately. Comparison is whitespace/case-insensitive
    (see ``_normalize_account_number``).
    """
    if not account_number:
        return rows, 0
    target = _normalize_account_number(account_number)
    kept: list[ParsedRow] = []
    skipped = 0
    for row in rows:
        if (
            not row.account_number
            or _normalize_account_number(row.account_number) == target
        ):
            kept.append(row)
        else:
            skipped += 1
    return kept, skipped


def find_duplicates(
    rows: list[ParsedRow], existing: list[Transaction], account_id: str
) -> tuple[list[ParsedRow], list[ParsedRow]]:
    """Split ``rows`` into ``(new, duplicate)`` against ``existing`` ledger rows.

    A row counts as a duplicate of an existing transaction on the same
    account with the same date, amount, and description (exact match,
    case/whitespace-insensitive on description) — the closest a CSV row
    (no natural id) has to a stable identity, guarding against
    re-importing the same or an overlapping statement.
    """
    existing_keys = {
        (txn.date, txn.amount, txn.description.strip().lower())
        for txn in existing
        if txn.account_id == account_id
    }
    new_rows: list[ParsedRow] = []
    duplicate_rows: list[ParsedRow] = []
    for row in rows:
        key = (row.date, row.amount, row.description.strip().lower())
        (duplicate_rows if key in existing_keys else new_rows).append(row)
    return new_rows, duplicate_rows


def build_transactions(
    rows: list[ParsedRow], account_id: str, rules: list[Rule]
) -> list[Transaction]:
    """Build ``Transaction`` rows from parsed CSV rows.

    Type is derived purely from the amount's sign (negative → expense,
    positive → income) — this matches the app's own signed-amount
    convention directly, so no sign-flipping is needed the way manual
    entry needs it (manual entry takes an unsigned magnitude plus an
    explicit type). A row whose description matches no rule (or no rules
    exist yet — Phase 4 hasn't shipped rule management) gets
    ``DEFAULT_CATEGORY`` rather than a blank category, so it's visibly
    flagged for the user to fix rather than rendering as an empty cell.
    """
    transactions = []
    for row in rows:
        category, subcategory = categorize(row.description, rules)
        transactions.append(
            Transaction(
                id=str(uuid.uuid4()),
                date=row.date,
                account_id=account_id,
                category=category or DEFAULT_CATEGORY,
                subcategory=subcategory,
                description=row.description,
                amount=row.amount,
                type=TransactionType.EXPENSE
                if row.amount < 0
                else TransactionType.INCOME,
                transfer_id=None,
                notes=None,
            )
        )
    return transactions
