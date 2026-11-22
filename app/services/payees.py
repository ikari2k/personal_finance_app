"""Payee management: group unnamed bank text, name it, rename and merge payees.

Pure functions over in-memory data — no file I/O. A *payee* is the clean name
of who was paid or paid you (``Transaction.payee``); the bank's own text stays
in ``description`` and is never touched here. The workflow is:

- ``unnamed_groups`` clusters transactions that have no payee by what their
  description says (store numbers and legal suffixes ignored), biggest first,
  with a suggested name.
- ``name_group`` gives one cluster a name — only on rows that have no payee, so
  an existing payee is never overwritten — and ``add_aliases`` remembers the
  exact spellings so an import names them too.
- ``rename_payee`` and ``merge_payees`` change an existing payee's name across
  the ledger, and ``retarget_aliases`` keeps the book pointing at the new name.

Transfers never have a payee and are skipped throughout.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.payee import PayeeAlias, PayeeBook
from app.models.transaction import Transaction, TransactionType

MAX_PAYEE_LENGTH = 100
# A group this big whose spellings never repeat looks like memo text (invoice
# numbers, payment titles), not a merchant.
MEMO_MIN_SPELLINGS = 5

# Company-form, web and filler words that do not tell one payee from another.
_NOISE_WORDS = {
    "sp", "z", "o", "oo", "s", "a", "sa", "spk", "ul", "pl", "k", "c",
    "com", "www", "eu", "org", "net",
}  # fmt: skip
_WORD_SPLIT = re.compile(r"[\W_]+")


def spelling_key(text: str) -> str:
    """Normalize a bank spelling for exact matching: case and spacing ignored."""
    return " ".join(text.split()).casefold()


def _tokens(text: str) -> list[str]:
    """Words of ``text`` that identify a payee: no store numbers, no company forms.

    Words split on any punctuation ("APPLE.COM/BILL" is "APPLE", "COM",
    "BILL"). A word containing a digit ("Z8540", "1035") is a store or terminal
    id, not a name; single letters, legal-form words and "com"/"www" are
    dropped too.
    """
    kept = []
    for word in _WORD_SPLIT.split(text):
        if len(word) < 2 or any(ch.isdigit() for ch in word):
            continue
        if word.casefold() in _NOISE_WORDS:
            continue
        kept.append(word)
    return kept


def group_key(text: str) -> str:
    """Cluster key for a description: its identifying words, lowercased.

    ``"ZABKA Z8540 K.2"`` and ``"ZABKA ZC026 K.1"`` both give ``"zabka"``. A
    description with no identifying word at all (only numbers) falls back to
    its own spelling key, so it forms a group of its own.
    """
    words = _tokens(text)
    return " ".join(w.casefold() for w in words) if words else spelling_key(text)


def clean_payee_name(raw: str) -> str:
    """Trim a typed payee name; raises ``ValueError`` if empty or too long."""
    name = " ".join(raw.split())
    if not name:
        raise ValueError("enter a payee name")
    if len(name) > MAX_PAYEE_LENGTH:
        raise ValueError(f"a payee name can be at most {MAX_PAYEE_LENGTH} characters")
    return name


def suggest_name(spellings: list[tuple[str, int]]) -> str:
    """Suggest a payee name for a group from its most common spelling.

    Takes the identifying words of that spelling, and title-cases them when the
    bank shouted them in capitals. ``""`` when nothing identifying is left.
    """
    if not spellings:
        return ""
    words = _tokens(spellings[0][0])
    if not words:
        return ""
    name = " ".join(words)
    return name.title() if name.isupper() else name


@dataclass
class UnnamedGroup:
    """Unnamed transactions that share a ``group_key``, ready to be named."""

    key: str
    suggestion: str
    spellings: list[tuple[str, int]]  # (bank text, count), most common first
    count: int
    spent: Decimal  # expense rows, as a positive amount
    received: Decimal  # income rows
    top_category: str
    last_seen: date
    memo_like: bool


@dataclass
class PayeeSummary:
    """One named payee's usage across the ledger."""

    name: str
    count: int
    spent: Decimal  # expense rows, as a positive amount
    received: Decimal  # income rows
    top_category: str
    last_seen: date
    spellings: list[tuple[str, int]]


def _is_candidate(txn: Transaction) -> bool:
    """Whether ``txn`` can carry a payee at all (not a transfer)."""
    return txn.type is not TransactionType.TRANSFER


def money_flow(transactions: list[Transaction]) -> tuple[Decimal, Decimal]:
    """Return ``(spent, received)`` for ``transactions``, both positive.

    A payee can sit on both sides (a shop that also refunds, an employer that
    is also reimbursed), so the two are kept apart instead of netted or added.
    """
    spent = sum(
        (-t.amount for t in transactions if t.type is TransactionType.EXPENSE),
        Decimal("0"),
    )
    received = sum(
        (t.amount for t in transactions if t.type is TransactionType.INCOME),
        Decimal("0"),
    )
    return spent, received


def _most_common(values: list[str]) -> str:
    return Counter(values).most_common(1)[0][0] if values else ""


def unnamed_groups(
    transactions: list[Transaction], book: PayeeBook
) -> list[UnnamedGroup]:
    """Cluster payee-less transactions by description, biggest group first.

    Skips transfers, blank descriptions and groups the user chose to leave
    unnamed (``book.skipped``). Ordered by transaction count, then by amount.
    """
    buckets: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if _is_candidate(txn) and not txn.payee and txn.description.strip():
            buckets[group_key(txn.description)].append(txn)
    skipped = set(book.skipped)
    groups = []
    for key, members in buckets.items():
        if key in skipped:
            continue
        counts = Counter(" ".join(t.description.split()) for t in members)
        spellings = counts.most_common()
        spent, received = money_flow(members)
        groups.append(
            UnnamedGroup(
                key=key,
                suggestion=suggest_name(spellings),
                spellings=spellings,
                count=len(members),
                spent=spent,
                received=received,
                top_category=_most_common([t.category for t in members]),
                last_seen=max(t.date for t in members),
                memo_like=(
                    len(spellings) >= MEMO_MIN_SPELLINGS and spellings[0][1] == 1
                ),
            )
        )
    groups.sort(key=lambda g: (-g.count, -(g.spent + g.received), g.key))
    return groups


def group_members(transactions: list[Transaction], key: str) -> list[Transaction]:
    """Return the payee-less, non-transfer transactions in group ``key``."""
    return [
        t
        for t in transactions
        if _is_candidate(t)
        and not t.payee
        and t.description.strip()
        and group_key(t.description) == key
    ]


def payee_summaries(transactions: list[Transaction]) -> list[PayeeSummary]:
    """Usage of every named payee, in no particular order."""
    buckets: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if _is_candidate(txn) and txn.payee:
            buckets[txn.payee].append(txn)
    summaries = []
    for name, members in buckets.items():
        counts = Counter(" ".join(t.description.split()) for t in members)
        spent, received = money_flow(members)
        summaries.append(
            PayeeSummary(
                name=name,
                count=len(members),
                spent=spent,
                received=received,
                top_category=_most_common([t.category for t in members]),
                last_seen=max(t.date for t in members),
                spellings=counts.most_common(),
            )
        )
    return summaries


def coverage(transactions: list[Transaction]) -> tuple[int, int]:
    """Return ``(named, total)`` over every transaction that can have a payee."""
    candidates = [t for t in transactions if _is_candidate(t)]
    return sum(1 for t in candidates if t.payee), len(candidates)


def name_group(
    transactions: list[Transaction], key: str, payee: str
) -> tuple[list[Transaction], int]:
    """Return ``transactions`` with group ``key``'s payee-less rows named ``payee``.

    Rows that already have a payee are never changed. Also returns how many
    rows were named. ``payee`` must already be cleaned (``clean_payee_name``).
    """
    member_ids = {t.id for t in group_members(transactions, key)}
    updated = [
        t.model_copy(update={"payee": payee}) if t.id in member_ids else t
        for t in transactions
    ]
    return updated, len(member_ids)


def add_aliases(book: PayeeBook, spellings: list[str], payee: str) -> PayeeBook:
    """Return ``book`` remembering that each of ``spellings`` means ``payee``.

    A spelling that already had an alias (compared with ``spelling_key``) is
    re-pointed at ``payee`` rather than duplicated.
    """
    named = {spelling_key(s): s for s in spellings}
    kept = [a for a in book.aliases if spelling_key(a.spelling) not in named]
    added = [PayeeAlias(spelling=s, payee=payee) for s in named.values()]
    return book.model_copy(update={"aliases": kept + added})


def alias_lookup(book: PayeeBook) -> dict[str, str]:
    """``spelling_key -> payee`` for every alias, for fast import-time lookup."""
    return {spelling_key(a.spelling): a.payee for a in book.aliases}


def rename_payee(
    transactions: list[Transaction], old: str, new: str
) -> tuple[list[Transaction], int]:
    """Return ``transactions`` with payee ``old`` changed to ``new``, and the count."""
    return merge_payees(transactions, [old], new)


def merge_payees(
    transactions: list[Transaction], names: list[str], new: str
) -> tuple[list[Transaction], int]:
    """Return ``transactions`` with every payee in ``names`` changed to ``new``.

    Also returns how many rows actually changed (rows already named ``new`` are
    not counted). Transfers are never touched.
    """
    sources = set(names)
    changed = 0
    result = []
    for txn in transactions:
        if _is_candidate(txn) and txn.payee in sources and txn.payee != new:
            result.append(txn.model_copy(update={"payee": new}))
            changed += 1
        else:
            result.append(txn)
    return result, changed


def retarget_aliases(book: PayeeBook, names: list[str], new: str) -> PayeeBook:
    """Return ``book`` with aliases for any of ``names`` pointing at ``new`` instead."""
    sources = set(names)
    aliases = [
        a.model_copy(update={"payee": new}) if a.payee in sources else a
        for a in book.aliases
    ]
    return book.model_copy(update={"aliases": aliases})


def skip_group(book: PayeeBook, key: str) -> PayeeBook:
    """Return ``book`` with group ``key`` left unnamed (hidden from the queue)."""
    if key in book.skipped:
        return book
    return book.model_copy(update={"skipped": [*book.skipped, key]})


def clear_skipped(book: PayeeBook) -> PayeeBook:
    """Return ``book`` with every skipped group back in the queue."""
    return book.model_copy(update={"skipped": []})
