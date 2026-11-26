"""Parse a bank CSV export into candidate ledger transactions.

Pure functions over in-memory data — no file I/O, matching every other
``services`` module (the router reads the uploaded file's bytes and any
existing ledger/mapping/rules via ``storage`` and passes them in here).

Imported rows are always income or expense, never transfers — pairing
never happens here, at parse time. A mapping can optionally capture the
bank's own counterparty-account-number column (``counterparty_account``/
``counterparty_account_fallback``), which is carried onto each
``Transaction`` unchanged; a *separate*, later, explicitly-triggered step
(``app.services.transactions.find_transfer_matches``) is what
cross-references that against the user's own registered accounts to spot
rows that are secretly a transfer, always previewed before merging two
rows into one. Plain amount/date proximity alone remains too fragile to
guess a transfer from automatically — that's not what happens here.
"""

import csv
import io
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date as date_
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.models.category import CategoriesByType
from app.models.import_mapping import ImportMapping
from app.models.payee import PayeeRule
from app.models.rule import Rule
from app.models.transaction import Transaction, TransactionType
from app.services.categorizer import find_matching_rule
from app.services.payees import field_values_of, match_payee, spelling_key

DEFAULT_CATEGORY = "Uncategorized"


# Optional mapping columns appended to the description, in this order, and the
# text put between them.
DESCRIPTION_EXTRA_KEYS = ("description_extra_1", "description_extra_2")
DESCRIPTION_JOINER = " | "


@dataclass
class ParsedRow:
    """One parsed-but-not-yet-committed CSV row."""

    date: date_
    description: str
    amount: Decimal
    account_number: str = ""
    counterparty_account: str = ""
    # ``description`` plus any ``description_extra_*`` columns, joined for
    # display; "" when nothing was appended. Only ever shown (the preview
    # pre-fills it, and keeping it makes it the transaction's note):
    # ``description`` stays the bank's own text, because that is what keys
    # duplicate detection and rule matching.
    display_description: str = ""
    # The bank's own name for the other party, when the mapping has a payee
    # column; a matching rule's payee takes precedence over it.
    payee: str = ""
    # Values of the mapping's labelled payee field columns for this row, by
    # label (blank values left out); stored on the transaction for payee rules.
    payee_fields: dict[str, str] = field(default_factory=dict)


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


def normalize_account_number(value: str) -> str:
    """Strip all whitespace and case so account numbers compare reliably.

    Bank exports format IBANs with spacing that may not match how the
    user typed ``Account.number`` into this app (see
    ``app.models.import_mapping.ImportMapping``'s ``account_number``
    column note). Shared with ``app.services.transactions
    .find_transfer_matches``, which compares a captured
    ``counterparty_account`` against every registered ``Account.number``
    the same way.
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
    description_fallback_idx = mapping.columns.get("description_fallback")
    description_extra_idxs = [
        mapping.columns[key] for key in DESCRIPTION_EXTRA_KEYS if key in mapping.columns
    ]
    payee_field_columns = [(pf.label, pf.column) for pf in mapping.payee_fields]
    payee_idx = mapping.columns.get("payee")
    payee_fallback_idx = mapping.columns.get("payee_fallback")
    counterparty_idx = mapping.columns.get("counterparty_account")
    counterparty_fallback_idx = mapping.columns.get("counterparty_account_fallback")

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
        description = row[description_idx].strip()
        if not description and description_fallback_idx is not None:
            description = row[description_fallback_idx].strip()
        # Optional extra columns are appended, each in turn, to whatever the
        # primary/fallback gave, to help the user recognise the row. They go
        # into ``display_description`` only, never ``description`` itself. A
        # value already in the text is not repeated.
        parts = [description] if description else []
        for idx in description_extra_idxs:
            extra = row[idx].strip()
            if extra and extra not in parts:
                parts.append(extra)
        display_description = DESCRIPTION_JOINER.join(parts) if len(parts) > 1 else ""
        # A bank export typically names the counterparty in one of two
        # mutually-exclusive columns depending on transaction direction
        # (e.g. Credit Agricole: "Rachunek nadawcy"/sender for an inbound
        # row, "Rachunek odbiorcy"/recipient for an outbound one) — same
        # primary+fallback collapse as description/description_fallback
        # above, not a second value to append.
        counterparty = (
            row[counterparty_idx].strip() if counterparty_idx is not None else ""
        )
        if not counterparty and counterparty_fallback_idx is not None:
            counterparty = row[counterparty_fallback_idx].strip()
        # The bank's name for the other party, same primary+fallback collapse
        # as the description; a rule's payee still wins at build time.
        payee = row[payee_idx].strip() if payee_idx is not None else ""
        if not payee and payee_fallback_idx is not None:
            payee = row[payee_fallback_idx].strip()
        payee_fields = {
            label: row[column].strip()
            for label, column in payee_field_columns
            if column < len(row) and row[column].strip()
        }
        parsed.append(
            ParsedRow(
                date=parse_date(row[date_idx], mapping.date_format),
                description=description,
                amount=amount,
                display_description=display_description,
                payee=payee,
                payee_fields=payee_fields,
                account_number=row[account_idx].strip()
                if account_idx is not None
                else "",
                counterparty_account=normalize_account_number(counterparty)
                if counterparty
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
    (see ``normalize_account_number``).
    """
    if not account_number:
        return rows, 0
    target = normalize_account_number(account_number)
    kept: list[ParsedRow] = []
    skipped = 0
    for row in rows:
        if (
            not row.account_number
            or normalize_account_number(row.account_number) == target
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
    re-importing the same or an overlapping statement. Always the bank's own
    ``description``: ``description_extra_*`` text never takes part, so adding
    extras to a mapping does not change what counts as already imported.
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


@dataclass
class EnrichmentPlan:
    """What filling payee fields on existing rows from a CSV would do."""

    updates: dict[str, dict[str, str]]  # transaction id -> its fields afterwards
    already_complete: int  # matched ledger rows that already had every field
    ambiguous: int  # CSV rows skipped: same date/amount/text, different values
    unmatched: int  # CSV rows with no ledger row (a normal import would add them)


def _enrichment_key(date: date_, amount: Decimal, description: str) -> tuple:
    """Return the identity that pairs a CSV row with a ledger row."""
    return (date, amount, spelling_key(description))


def merge_payee_fields(
    wanted: dict[str, str], existing: dict[str, str]
) -> dict[str, str]:
    """Return ``existing`` filled out with ``wanted``, never changing a saved value.

    Labels follow ``wanted``'s order (the mapping's priority order, which decides
    what a transaction is grouped on); labels only ``existing`` has come after.
    """
    merged = {label: existing.get(label, value) for label, value in wanted.items()}
    for label, value in existing.items():
        merged.setdefault(label, value)
    return merged


def plan_enrichment(
    rows: list[ParsedRow], ledger: list[Transaction], account_id: str
) -> EnrichmentPlan:
    """Pair CSV rows with existing ledger rows and plan the payee fields to fill in.

    A CSV row matches a ledger row on the same account with the same date,
    amount and bank text (the same identity duplicate detection uses). Only
    labels a row is missing are added; saved values are never changed. When CSV
    rows share that identity but disagree on their field values, the pairing is
    ambiguous and none of them is applied.
    """
    by_key: dict[tuple, list[dict[str, str]]] = {}
    for row in rows:
        key = _enrichment_key(row.date, row.amount, row.description)
        by_key.setdefault(key, []).append(row.payee_fields)
    ledger_keys = {
        _enrichment_key(t.date, t.amount, t.description)
        for t in ledger
        if t.account_id == account_id
    }
    unmatched = sum(
        len(group) for key, group in by_key.items() if key not in ledger_keys
    )
    ambiguous = sum(
        len(group)
        for key, group in by_key.items()
        if key in ledger_keys and any(f != group[0] for f in group)
    )
    updates: dict[str, dict[str, str]] = {}
    complete = 0
    for txn in ledger:
        if txn.account_id != account_id:
            continue
        group = by_key.get(_enrichment_key(txn.date, txn.amount, txn.description))
        if not group or any(f != group[0] for f in group) or not group[0]:
            continue
        merged = merge_payee_fields(group[0], txn.payee_fields)
        if merged == txn.payee_fields:
            complete += 1
        else:
            updates[txn.id] = merged
    return EnrichmentPlan(updates, complete, ambiguous, unmatched)


def apply_enrichment(
    transactions: list[Transaction], updates: dict[str, dict[str, str]]
) -> tuple[list[Transaction], int]:
    """Return ``transactions`` with ``updates`` merged in, and how many rows changed.

    Re-merges against the rows as they are now, so a value saved since the
    preview is never overwritten.
    """
    changed = 0
    result = []
    for txn in transactions:
        wanted = updates.get(txn.id)
        merged = merge_payee_fields(wanted, txn.payee_fields) if wanted else None
        if merged is not None and merged != txn.payee_fields:
            result.append(txn.model_copy(update={"payee_fields": merged}))
            changed += 1
        else:
            result.append(txn)
    return result, changed


def build_transactions(
    rows: list[ParsedRow],
    account_id: str,
    rules: list[Rule],
    payee_rules: list[PayeeRule] | None = None,
    source: str = "",
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
        txn_type = TransactionType.EXPENSE if row.amount < 0 else TransactionType.INCOME
        rule = find_matching_rule(row.description, row.amount, txn_type, rules)
        category, subcategory = (rule.category, rule.subcategory) if rule else ("", "")
        # Payee precedence: a category rule's curated name, then a payee rule
        # from the payee view (matched on the row's fields: the
        # mapping's labelled columns and the description), then the
        # bank's own name for the other party.
        payee_fields = row.payee_fields
        payee = (
            (rule.payee if rule and rule.payee else "")
            or match_payee(
                payee_rules or [], field_values_of(payee_fields, row.description)
            )
            or row.payee
        ).strip()
        transactions.append(
            Transaction(
                id=str(uuid.uuid4()),
                date=row.date,
                account_id=account_id,
                category=category or DEFAULT_CATEGORY,
                subcategory=subcategory,
                description=row.description,
                amount=row.amount,
                type=txn_type,
                transfer_id=None,
                # Preview-only: pre-fills the editable description box. The
                # confirm step saves it as the note when it differs from
                # ``description`` (see ``apply_note_edits``).
                notes=row.display_description or None,
                counterparty_account=row.counterparty_account,
                payee=payee,
                payee_fields=payee_fields,
                source=source,
            )
        )
    return transactions


def apply_category_picks(
    rows: list[dict], picks: list[str], categories: CategoriesByType
) -> list[dict]:
    """Return ``rows`` with the category/subcategory the user re-picked in the preview.

    ``picks`` holds one JSON ``[category, subcategory]`` string per row, in
    row order. Like the transactions list's inline picker, this never
    creates a category: a pick is applied only when that pair already
    exists in the row's own (income/expense, by sign) tree; anything else
    — malformed, unknown, or a list that doesn't line up with ``rows`` —
    leaves the row's rule-assigned category untouched.
    """
    if len(picks) != len(rows):
        return rows
    result = []
    for row, pick in zip(rows, picks):
        txn_type = "expense" if Decimal(row["amount"]) < 0 else "income"
        try:
            category, subcategory = json.loads(pick)
            entry = categories.get(txn_type, {}).get(category)
            valid = entry is not None and (
                not subcategory or subcategory in entry["subcategories"]
            )
        except (ValueError, TypeError):
            valid = False
        result.append(
            {**row, "category": category, "subcategory": subcategory} if valid else row
        )
    return result


def apply_payee_edits(rows: list[dict], values: list[str]) -> list[dict]:
    """Return ``rows`` with the payee the user typed (or cleared) in the preview.

    ``values`` holds one text per row, in row order, pre-filled with the
    payee the rule or mapping gave. The typed text, trimmed, is the payee,
    blank included (clearing a suggestion is a choice). A list that doesn't
    line up with ``rows`` is ignored, same as ``apply_note_edits``.
    """
    if len(values) != len(rows):
        return rows
    return [{**row, "payee": value.strip()} for row, value in zip(rows, values)]


def apply_note_edits(rows: list[dict], values: list[str]) -> list[dict]:
    """Return ``rows`` with ``notes`` set where the preview's description was edited.

    ``values`` holds one text per row, in row order, pre-filled with the
    bank's description. ``description`` itself is never changed (it keys
    duplicate detection and rule matching); a differing, non-blank value
    becomes the row's ``notes`` instead, same as editing it on the
    transactions list. A list that doesn't line up with ``rows`` is ignored.
    """
    if len(values) != len(rows):
        return rows
    return [
        {**row, "notes": value.strip()}
        if value.strip() and value.strip() != row["description"]
        else row
        for row, value in zip(rows, values)
    ]
