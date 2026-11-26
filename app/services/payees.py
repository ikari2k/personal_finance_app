"""Payee management: group unnamed rows, name them with rules, rename and merge.

Pure functions over in-memory data — no file I/O. A *payee* is the clean name of
who was paid or paid you (``Transaction.payee``); the bank's own text stays in
``description`` and is never touched here.

Payees are recognised by the *payee fields* the user configured on the import
mapping (``Transaction.payee_fields``: a shop's web address, the other side's
name, ...) and, as one more field called "Description", by the transaction's
own description. That last one is how rows imported before payee fields were
saved can be named; it is always offered, as the last field. A
``PayeeRule`` says "if all of these fields have these values, the
payee is X". When several rules match one row the one with more fields wins,
then the newer one.

The workflow:

- ``unnamed_groups`` clusters the payee-less rows that have a value for one chosen
  payee field, biggest first, with a suggested name. Each payee field has its
  own set of suggestions, so a row can be offered under several fields; naming
  it under one takes it out of all of them.
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
    DESCRIPTION_FIELD,
    MAX_PAYEE_FIELDS,
    PayeeBook,
    PayeeRule,
)
from app.models.transaction import Transaction, TransactionType

MAX_PAYEE_LENGTH = 100
CARD_SPELLINGS = 3  # spellings a group's card lists before "+ N more"
# Prefix of a group's cluster that stands for exactly one spelling (a divided group).
EXACT_PREFIX = "exact:"
# A group key is "<field label>::<cluster>": the same cluster under two fields is
# two different groups.
KEY_SEP = "::"
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
        label = " ".join(raw_label.replace(KEY_SEP, ":").split()) or f"Field {number}"
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
    payee_fields: dict[str, str], description: str = ""
) -> dict[str, str]:
    """Return the non-blank payee field values a rule can match on, by label.

    The mapped fields come in the order the mapping configured them, which is
    their priority; the description follows as the ``Description`` field unless it
    only repeats one of them.
    """
    values = {
        label: value.strip() for label, value in payee_fields.items() if value.strip()
    }
    # A description that just repeats a mapped field adds nothing to group on.
    repeats = {spelling_key(value) for value in values.values()}
    if (
        description.strip()
        and DESCRIPTION_FIELD not in values
        and spelling_key(description) not in repeats
    ):
        values[DESCRIPTION_FIELD] = description.strip()
    return values


def field_values(txn: Transaction) -> dict[str, str]:
    """Return ``txn``'s non-blank payee field values by label, description included."""
    return field_values_of(txn.payee_fields, txn.description)


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
    """Unnamed transactions that share a cluster of one payee field's values."""

    key: str  # "<label>::<cluster>", see ``make_key``
    label: str  # the payee field this group is built from
    suggestion: str
    spellings: list[tuple[str, int]]  # (value, count), most common first
    count: int
    spent: Decimal  # expense rows, as a positive amount
    received: Decimal  # income rows
    top_category: str
    last_seen: date
    memo_like: bool
    base_key: str  # the key the group would have if it were not divided
    other_fields: list[tuple[str, int]]  # other payee fields, rows having each

    @property
    def exact(self) -> bool:
        """Whether this group is one spelling split out of a bigger group."""
        return split_key(self.key)[1].startswith(EXACT_PREFIX)

    @property
    def has_more_detail(self) -> bool:
        """Whether opening the group's workspace would show anything new.

        True when its rows also have values in other payee fields (so there is
        something to split or narrow by), or when it has more spellings than its
        card lists.
        """
        return bool(self.other_fields) or len(self.spellings) > CARD_SPELLINGS


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


def make_key(label: str, cluster: str) -> str:
    """Build a group key from the payee field and the cluster within it."""
    return f"{label}{KEY_SEP}{cluster}"


def split_key(key: str) -> tuple[str, str]:
    """Return ``(label, cluster)`` of a group key; ``("", key)`` if it has no field."""
    label, sep, cluster = key.partition(KEY_SEP)
    return (label, cluster) if sep else ("", key)


def field_text(txn: Transaction, label: str) -> str:
    """Return ``txn``'s value for payee field ``label``; ``""`` if it has none."""
    return " ".join(field_values(txn).get(label, "").split())


def _is_candidate(txn: Transaction) -> bool:
    """Whether ``txn`` can carry a payee at all (not a transfer)."""
    return txn.type is not TransactionType.TRANSFER


def _is_unnamed(txn: Transaction, label: str) -> bool:
    """Whether ``txn`` awaits a payee and has a value for payee field ``label``."""
    return _is_candidate(txn) and not txn.payee and bool(field_text(txn, label))


def has_any_payee_field(txn: Transaction) -> bool:
    """Whether ``txn`` has a value in at least one mapped payee field.

    The description does not count: it is always there as a fallback field.
    """
    return bool(field_values_of(txn.payee_fields))


def without_payee_fields(transactions: list[Transaction]) -> int:
    """Count payee-less transactions that have no payee field value at all.

    They cannot be grouped or named; the fill tool can give them values.
    """
    return sum(
        1
        for t in transactions
        if _is_candidate(t) and not t.payee and not has_any_payee_field(t)
    )


def row_group_key(txn: Transaction, label: str, split: set[str]) -> str:
    """Return the group ``txn`` belongs to under payee field ``label``.

    Normally the cluster of its value for that field. When that cluster was
    divided (``split``), the row is its own group: its exact value, prefixed so
    it can never collide with a cluster.
    """
    text = field_text(txn, label)
    cluster = group_key(text)
    if make_key(label, cluster) in split:
        cluster = EXACT_PREFIX + spelling_key(text)
    return make_key(label, cluster)


def base_group_key(key: str) -> str:
    """Return the undivided cluster key that group ``key`` belongs to."""
    label, cluster = split_key(key)
    if cluster.startswith(EXACT_PREFIX):
        cluster = group_key(cluster[len(EXACT_PREFIX) :])
    return make_key(label, cluster) if label else cluster


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


def distinct_spellings(texts: list[str]) -> list[tuple[str, int]]:
    """Return ``(text, count)`` per spelling, most common first.

    Spellings that differ only in case or spacing are one spelling (the same
    rule matching uses), shown as they were most often written.
    """
    by_key: dict[str, Counter[str]] = {}
    for text in texts:
        by_key.setdefault(spelling_key(text), Counter())[text] += 1
    spellings = [
        (counts.most_common(1)[0][0], sum(counts.values()))
        for counts in by_key.values()
    ]
    return sorted(spellings, key=lambda item: (-item[1], item[0]))


def _most_common(values: list[str]) -> str:
    return Counter(values).most_common(1)[0][0] if values else ""


def payee_field_labels(
    transactions: list[Transaction], configured: list[str]
) -> list[str]:
    """Return the payee fields to offer suggestions for, in a stable order.

    The labels the import mappings configure come first, in their order; any
    other label that rows carry (a field since removed from a mapping) follows,
    most used first.
    """
    seen = list(dict.fromkeys(configured))
    extra = Counter(
        label
        for t in transactions
        for label in field_values(t)
        if label not in seen and label != DESCRIPTION_FIELD
    )
    return seen + [label for label, _ in extra.most_common()] + [DESCRIPTION_FIELD]


def unnamed_groups(
    transactions: list[Transaction], book: PayeeBook, label: str
) -> list[UnnamedGroup]:
    """Cluster the payee-less rows that have a value for field ``label``.

    Biggest group first (transaction count, then amount). Skips transfers and
    groups the user chose to leave unnamed (``book.skipped``). Each group also
    reports how many of its rows have a value in each of the other payee fields.
    """
    split = set(book.split)
    buckets: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if _is_unnamed(txn, label):
            buckets[row_group_key(txn, label, split)].append(txn)
    skipped = set(book.skipped)
    groups = []
    for key, members in buckets.items():
        if key in skipped:
            continue
        spellings = distinct_spellings([field_text(t, label) for t in members])
        spent, received = money_flow(members)
        others = Counter(
            other for t in members for other in field_values(t) if other != label
        )
        groups.append(
            UnnamedGroup(
                key=key,
                label=label,
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
                base_key=base_group_key(key),
                other_fields=sorted(others.items(), key=lambda i: (-i[1], i[0])),
            )
        )
    groups.sort(key=lambda g: (-g.count, -(g.spent + g.received), g.key))
    return groups


def group_members(transactions: list[Transaction], key: str) -> list[Transaction]:
    """Return the payee-less, non-transfer transactions in group ``key``.

    ``key`` is ``"<label>::<cluster>"``, the cluster being a cluster key or an
    ``exact:`` key for one value of a divided group.
    """
    label, cluster = split_key(key)
    if not label:
        return []
    exact = cluster.startswith(EXACT_PREFIX)
    matches = spelling_key if exact else group_key
    wanted = cluster[len(EXACT_PREFIX) :] if exact else cluster
    return [
        t
        for t in transactions
        if _is_unnamed(t, label) and matches(field_text(t, label)) == wanted
    ]


@dataclass
class FieldOption:
    """One field a rule could be built from, summarised over a group's rows."""

    label: str
    values: list[tuple[str, int]]  # distinct values and counts, most common first
    present: int  # how many of the group's rows have this field
    total: int  # how many rows the group has


def field_options(members: list[Transaction]) -> list[FieldOption]:
    """Summarise the payee fields a rule for this group can be built from.

    Only the payee fields the import mapping configured, in the order their rows
    list them, each with how many rows have it and its values.
    """
    per_label: dict[str, Counter[str]] = {}
    for txn in members:
        for label, value in field_values(txn).items():
            per_label.setdefault(label, Counter())[" ".join(value.split())] += 1
    options = []
    for label, counts in per_label.items():
        options.append(
            FieldOption(
                label=label,
                values=counts.most_common(),
                present=sum(counts.values()),
                total=len(members),
            )
        )
    return options


def default_labels(key: str, members: list[Transaction]) -> list[str]:
    """Which fields a naming rule uses unless the user changes it.

    The payee field the group is built from: naming a group of web addresses
    makes a rule on the web address.
    """
    label = split_key(key)[0]
    return [label] if label and any(label in field_values(t) for t in members) else []


@dataclass
class Combination:
    """One distinct combination of the ticked fields' values within a group."""

    values: dict[str, str]  # label -> value (as most often spelled)
    count: int
    spent: Decimal
    received: Decimal
    others: dict[str, str]  # the most common value of each field not ticked


def combinations(
    members: list[Transaction], labels: list[str]
) -> tuple[list[Combination], int]:
    """Split a group's rows by the values of ``labels``, biggest combination first.

    Returns the combinations and how many rows lack one of the labels (they
    cannot be told apart on those fields, so they appear in no combination).
    Each combination also carries the commonest value of every other field its
    rows have, so what a rule leaves out stays visible.
    """
    buckets: dict[tuple[str, ...], list[tuple[Transaction, dict[str, str]]]] = {}
    without = 0
    for txn in members:
        values = field_values(txn)
        if labels and all(label in values for label in labels):
            combo = tuple(spelling_key(values[label]) for label in labels)
            buckets.setdefault(combo, []).append((txn, values))
        else:
            without += 1
    result = []
    for rows in buckets.values():
        shown = {
            label: _most_common([" ".join(v[label].split()) for _, v in rows])
            for label in labels
        }
        other_labels = {
            lb
            for _, v in rows
            for lb in v
            if lb not in labels and lb not in BUILT_IN_FIELDS
        }
        others = {
            label: _most_common(
                [" ".join(v[label].split()) for _, v in rows if label in v]
            )
            for label in sorted(other_labels)
        }
        spent, received = money_flow([t for t, _ in rows])
        result.append(
            Combination(
                values=shown,
                count=len(rows),
                spent=spent,
                received=received,
                others=others,
            )
        )
    result.sort(
        key=lambda c: (-c.count, -(c.spent + c.received), sorted(c.values.items()))
    )
    return result, without


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
    only: dict[str, str] | None = None,
) -> NamingPlan:
    """Plan naming group ``key`` as ``payee`` with a rule on ``labels``.

    ``only`` narrows it to one combination: just the group's rows whose fields
    have exactly those values (its labels replace ``labels``), giving one rule.

    One rule is made per distinct combination of the chosen fields' values among
    the group's rows (a group of 25 spellings and a rule on the description
    gives 25 rules). The rows named are every payee-less row where one of those
    new rules is the one that wins, including rows outside the group that share
    the same values; a row that an older, more specific rule claims is left to
    that rule. Group rows missing a chosen field cannot form a rule and stay
    unnamed.
    """
    members = group_members(transactions, key)
    if only is not None:
        labels = list(only)
        wanted = {label: spelling_key(value) for label, value in only.items()}
        members = [
            t
            for t in members
            if all(
                label in (values := field_values(t))
                and spelling_key(values[label]) == wanted[label]
                for label in labels
            )
        ]
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
        if not _is_candidate(txn) or txn.payee:
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


def split_group(book: PayeeBook, key: str) -> PayeeBook:
    """Return ``book`` with cluster ``key`` divided into one group per spelling."""
    key = base_group_key(key)
    if key in book.split:
        return book
    return book.model_copy(update={"split": [*book.split, key]})


def unsplit_group(book: PayeeBook, key: str) -> PayeeBook:
    """Return ``book`` with ``key``'s cluster grouped as one again."""
    key = base_group_key(key)
    return book.model_copy(update={"split": [k for k in book.split if k != key]})


def skip_group(book: PayeeBook, key: str) -> PayeeBook:
    """Return ``book`` with group ``key`` left unnamed (hidden from the queue)."""
    if key in book.skipped:
        return book
    return book.model_copy(update={"skipped": [*book.skipped, key]})


def clear_skipped(book: PayeeBook) -> PayeeBook:
    """Return ``book`` with every skipped group back in the queue."""
    return book.model_copy(update={"skipped": []})
