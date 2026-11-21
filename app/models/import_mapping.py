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
    ``amount``. Optional:
    - ``account_number`` — when present, an import filters the file
      down to rows whose value in that column matches the destination
      account's own ``Account.number``, since a single export can mix
      rows from more than one of the bank's own accounts (see the
      Credit Agricole sample, which interleaves a checking account and
      its linked credit card sub-account in one file).
    - ``description_fallback`` — when the primary ``description``
      column is blank for a row, its value is used instead (e.g. a
      wire transfer's "Miejsce transakcji"/merchant-name column is
      blank, but "Odbiorca"/payee or "Tytuł"/memo has the useful text).
      Never used when the primary column has *any* value, blank or not
      — this is a fallback for a sparse primary column, not a second
      column to append.
    - ``description_extra_1``, ``description_extra_2`` — columns appended
      (after the primary/fallback text, joined with `` | ``) to help the
      user recognise a row, e.g. a merchant's web address or a
      detailed-description column. They never change ``description``
      (which keys duplicate detection and rule matching): the combined
      text pre-fills the import preview's description box and is saved as
      the row's note. Blank cells and values already in the text are
      skipped. Unlike the fallback these are appended even when the
      primary column has a value.

    - ``payee``, ``payee_fallback`` — the bank's own name for the other party
      (e.g. a card export's merchant column, else its recipient/sender
      column), same primary+fallback shape as ``description``. It becomes
      ``Transaction.payee`` unless a matching rule names a payee, and the
      import preview lets the user edit it before it is saved.

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
