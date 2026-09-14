"""Domain model for a saved bank CSV import mapping.

Captured once per bank (via the import mapping-setup UI, Phase 3) and
reused automatically on every later import from that bank — see
``app.storage.import_mappings`` for the one-file-per-bank persistence and
``app.services.importer`` for how a mapping is applied to parse a file.
"""

from pydantic import BaseModel


class ImportMapping(BaseModel):
    """How to parse one bank's CSV export into ledger rows.

    ``columns`` maps a ledger-side field name to a **0-based column
    index** in the bank's CSV, not a column name — real bank exports
    (see the Credit Agricole sample this was designed against) can have
    duplicate header names across columns that mean different things, so
    a name isn't a reliable key. The mapping-setup UI still shows each
    column's header text and a sample value to pick from; only the
    resolved index is persisted. Required keys: ``date``, ``description``,
    ``amount``. Optional: ``account_number`` — when present, an import
    filters the file down to rows whose value in that column matches the
    destination account's own ``Account.number``, since a single export
    can mix rows from more than one of the bank's own accounts (see the
    Credit Agricole sample, which interleaves a checking account and its
    linked credit card sub-account in one file).

    ``decimal_separator`` normalizes amounts like ``"-5,99 PLN"`` before
    parsing: everything except digits, ``-``, and this separator is
    stripped, then the separator itself is normalized to ``.`` — this
    handles a currency-code suffix and any thousands-separator character
    generically, without a separate "strip currency symbol" setting.
    """

    bank: str
    delimiter: str = ","
    encoding: str = "utf-8"
    date_format: str = "%Y-%m-%d"
    decimal_separator: str = "."
    columns: dict[str, int]
