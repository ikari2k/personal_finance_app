"""Payee management: group unnamed rows, name them with rules, rename and merge.

Pure functions over in-memory data — no file I/O. A *payee* is the clean name of
who was paid or paid you (``Transaction.payee``); the bank's own text stays in
``description`` and is never touched here.

Every transaction exposes *fields* a rule can match on (``field_values``): the
bank text (``Description``), the counterparty account, and whatever labelled
columns its import mapping captured (``Transaction.payee_fields``, e.g. a
shop's web address). A ``PayeeRule`` says "if all of these fields have these
values, the payee is X". When several rules match one row the one with more
fields wins, then the newer one.

The workflow:

- ``unnamed_groups`` clusters payee-less rows by their main identifying text
  (the first captured field, else the description), biggest first, with a
  suggested name.
- ``plan_naming`` turns "name this group, using these fields" into the rules to
  save and the rows they would name; the preview and the apply step both use it,
  so they cannot disagree. Rows that already have a payee are never changed.
- ``rename_payee``/``merge_payees`` change an existing payee's name across the
  ledger, and ``retarget_rules`` keeps the book pointing at the new name.

Transfers never have a payee and are skipped throughout.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.import_mapping import PayeeFieldColumn
from app.models.payee import (
    BUILT_IN_FIELDS,
    COUNTERPARTY_FIELD,
    DESCRIPTION_FIELD,
    MAX_PAYEE_FIELDS,
    PayeeBook,
    PayeeRule,
)
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
    """Normalize a value for exact matching: case and spacing ignored."""
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
    """Cluster key for a text: its identifying words, lowercased.

    ``"ZABKA Z8540 K.2"`` and ``"ZABKA ZC026 K.1"`` both give ``"zabka"``. A text
    with no identifying word at all (only numbers) falls back to its own
    spelling key, so it forms a group of its own.
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


def normalize_payee_fields(pairs: list[tuple[str, int]]) -> list[PayeeFieldColumn]:
    """Turn typed ``(label, column)`` pairs into a mapping's payee field columns.

    At most ``MAX_PAYEE_FIELDS`` are kept. A blank label becomes ``"Field N"``,
    and a label that repeats an earlier one or a built-in field (``Description``,
    ``Counterparty account``) gets a number appended, so labels stay unique
    (rules and stored values refer to them).
    """
    taken = {label.casefold() for label in BUILT_IN_FIELDS}
    fields: list[PayeeFieldColumn] = []
    for number, (raw_label, column) in enumerate(pairs[:MAX_PAYEE_FIELDS], start=1):
        label = " ".join(raw_label.split()) or f"Field {number}"
        candidate, suffix = label, 2
        while candidate.casefold() in taken:
            candidate = f"{label} {suffix}"
            suffix += 1
        taken.add(candidate.casefold())
        fields.append(PayeeFieldColumn(label=candidate, column=column))
    return fields


# ---------------------------------------------------------------------------
# Fields and rule matching
# ---------------------------------------------------------------------------


def field_values_of(
    description: str, counterparty_account: str, payee_fields: dict[str, str]
) -> dict[str, str]:
    """Return the non-blank field values a rule can match on, by label.

    The built-in fields first (``Description``, ``Counterparty account``), then
    the mapping's labelled fields in the order they were configured.
    """
    values: dict[str, str] = {}
    if description.strip():
        values[DESCRIPTION_FIELD] = description.strip()
    if counterparty_account.strip():
        values[COUNTERPARTY_FIELD] = counterparty_account.strip()
    for label, value in payee_fields.items():
        if value.strip():
            values[label] = value.strip()
    return values


def field_values(txn: Transaction) -> dict[str, str]:
    """Return ``txn``'s non-blank field values by label (see ``field_values_of``)."""
    return field_values_of(txn.description, txn.counterparty_account, txn.payee_fields)


def _rule_matches(rule: PayeeRule, values: dict[str, str]) -> bool:
    """Whether every field of ``rule`` is present in ``values`` with the same value."""
    return bool(rule.fields) and all(
        label in values and spelling_key(values[label]) == spelling_key(value)
        for label, value in rule.fields.items()
    )


def best_rule_index(rules: list[PayeeRule], values: dict[str, str]) -> int | None:
    """Index of the rule that wins for ``values``, or ``None`` if none match.

    More fields win (a rule on description and web address beats one on the
    description alone), and among equals the newer one, i.e. the later in
    ``rules``.
    """
    best: tuple[int, int] | None = None
    for index, rule in enumerate(rules):
        if _rule_matches(rule, values):
            candidate = (len(rule.fields), index)
            if best is None or candidate > best:
                best = candidate
    return None if best is None else best[1]


def match_payee(rules: list[PayeeRule], values: dict[str, str]) -> str | None:
    """Return the payee the winning rule gives ``values``, or ``None``."""
    index = best_rule_index(rules, values)
    return None if index is None else rules[index].payee


def _rule_key(rule: PayeeRule) -> frozenset[tuple[str, str]]:
    """Identity of a rule's condition: its labels and normalized values."""
    return frozenset(
        (label, spelling_key(value)) for label, value in rule.fields.items()
    )


def add_rules(book: PayeeBook, new_rules: list[PayeeRule]) -> PayeeBook:
    """Return ``book`` with ``new_rules`` added as its newest rules.

    A new rule with the same condition as an old one replaces it (the payee is
    re-pointed) rather than duplicating it.
    """
    replaced = {_rule_key(rule) for rule in new_rules}
    kept = [rule for rule in book.rules if _rule_key(rule) not in replaced]
    return book.model_copy(update={"rules": kept + list(new_rules)})


# ---------------------------------------------------------------------------
# Groups of unnamed rows
# ---------------------------------------------------------------------------


@dataclass
class UnnamedGroup:
    """Unnamed transactions that share a ``group_key``, ready to be named."""

    key: str
    suggestion: str
    primary_label: str  # the field the group was formed on
    spellings: list[tuple[str, int]]  # (main text, count), most common first
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


def primary_field(txn: Transaction) -> tuple[str, str]:
    """Return ``(label, text)`` a row is grouped on.

    The first captured payee field with a value, since that is what the user
    configured to recognise payees by; otherwise the bank text.
    """
    for label, value in txn.payee_fields.items():
        if value.strip():
            return label, " ".join(value.split())
    return DESCRIPTION_FIELD, " ".join(txn.description.split())


def _is_candidate(txn: Transaction) -> bool:
    """Whether ``txn`` can carry a payee at all (not a transfer)."""
    return txn.type is not TransactionType.TRANSFER


def _is_unnamed(txn: Transaction) -> bool:
    """Whether ``txn`` awaits a payee and has something to recognise it by."""
    return _is_candidate(txn) and not txn.payee and bool(primary_field(txn)[1])


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
    """Cluster payee-less transactions by their main text, biggest group first.

    Skips transfers, rows with nothing to recognise them by and groups the user
    chose to leave unnamed (``book.skipped``). Ordered by transaction count,
    then by amount.
    """
    buckets: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if _is_unnamed(txn):
            buckets[group_key(primary_field(txn)[1])].append(txn)
    skipped = set(book.skipped)
    groups = []
    for key, members in buckets.items():
        if key in skipped:
            continue
        counts = Counter(primary_field(t)[1] for t in members)
        spellings = counts.most_common()
        spent, received = money_flow(members)
        groups.append(
            UnnamedGroup(
                key=key,
                suggestion=suggest_name(spellings),
                primary_label=_most_common([primary_field(t)[0] for t in members]),
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
        if _is_unnamed(t) and group_key(primary_field(t)[1]) == key
    ]


@dataclass
class FieldOption:
    """One field a rule could be built from, summarised over a group's rows."""

    label: str
    values: list[tuple[str, int]]  # distinct values and counts, most common first
    present: int  # how many of the group's rows have this field
    total: int  # how many rows the group has


def field_options(members: list[Transaction]) -> list[FieldOption]:
    """Summarise every field the group's rows have, built-ins first.

    Always lists ``Description``; the counterparty account and any labelled
    field appear only when at least one row has a value for it.
    """
    per_label: dict[str, Counter[str]] = {}
    for txn in members:
        for label, value in field_values(txn).items():
            per_label.setdefault(label, Counter())[" ".join(value.split())] += 1
    labels = [DESCRIPTION_FIELD, COUNTERPARTY_FIELD]
    labels += [lb for lb in per_label if lb not in BUILT_IN_FIELDS]
    options = []
    for label in labels:
        counts = per_label.get(label)
        if counts is None and label != DESCRIPTION_FIELD:
            continue
        counts = counts or Counter()
        options.append(
            FieldOption(
                label=label,
                values=counts.most_common(),
                present=sum(counts.values()),
                total=len(members),
            )
        )
    return options


def default_labels(members: list[Transaction]) -> list[str]:
    """Which fields a naming rule uses unless the user changes it.

    The field the group was formed on, so naming a group of web addresses
    makes a rule on the web address and naming one of descriptions makes a
    rule on the description.
    """
    return [_most_common([primary_field(t)[0] for t in members]) or DESCRIPTION_FIELD]


@dataclass
class NamingPlan:
    """What naming a group with chosen fields does, before anything is written."""

    rules: list[PayeeRule]  # the rules to save
    rows: list[Transaction]  # the rows that would be named (as they are now)
    rows_without_fields: int  # group rows lacking a chosen field: left unnamed


def plan_naming(
    transactions: list[Transaction],
    book: PayeeBook,
    key: str,
    labels: list[str],
    payee: str,
) -> NamingPlan:
    """Plan naming group ``key`` as ``payee`` with a rule on ``labels``.

    One rule is made per distinct combination of the chosen fields' values among
    the group's rows (a group of 25 spellings and a rule on the description
    gives 25 rules). The rows named are every payee-less row where one of those
    new rules is the one that wins, including rows outside the group that share
    the same values; a row that an older, more specific rule claims is left to
    that rule. Group rows missing a chosen field cannot form a rule and stay
    unnamed.
    """
    members = group_members(transactions, key)
    rules_by_key: dict[tuple[str, ...], PayeeRule] = {}
    without = 0
    for txn in members:
        values = field_values(txn)
        if labels and all(label in values for label in labels):
            combo = tuple(spelling_key(values[label]) for label in labels)
            rules_by_key.setdefault(
                combo,
                PayeeRule(
                    fields={label: values[label] for label in labels}, payee=payee
                ),
            )
        else:
            without += 1
    new_rules = list(rules_by_key.values())
    combined = add_rules(book, new_rules).rules
    first_new = len(combined) - len(new_rules)
    rows = []
    for txn in transactions:
        if not _is_unnamed(txn):
            continue
        winner = best_rule_index(combined, field_values(txn))
        if winner is not None and winner >= first_new:
            rows.append(txn)
    return NamingPlan(rules=new_rules, rows=rows, rows_without_fields=without)


def apply_naming(
    transactions: list[Transaction], plan: NamingPlan, payee: str
) -> list[Transaction]:
    """Return ``transactions`` with ``plan``'s rows named ``payee``."""
    ids = {t.id for t in plan.rows}
    return [
        t.model_copy(update={"payee": payee}) if t.id in ids else t
        for t in transactions
    ]


# ---------------------------------------------------------------------------
# Named payees
# ---------------------------------------------------------------------------


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


def retarget_rules(book: PayeeBook, names: list[str], new: str) -> PayeeBook:
    """Return ``book`` with rules for any of ``names`` pointing at ``new`` instead."""
    sources = set(names)
    rules = [
        r.model_copy(update={"payee": new}) if r.payee in sources else r
        for r in book.rules
    ]
    return book.model_copy(update={"rules": rules})


def skip_group(book: PayeeBook, key: str) -> PayeeBook:
    """Return ``book`` with group ``key`` left unnamed (hidden from the queue)."""
    if key in book.skipped:
        return book
    return book.model_copy(update={"skipped": [*book.skipped, key]})


def clear_skipped(book: PayeeBook) -> PayeeBook:
    """Return ``book`` with every skipped group back in the queue."""
    return book.model_copy(update={"skipped": []})
