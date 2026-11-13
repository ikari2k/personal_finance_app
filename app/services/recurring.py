"""Spot recurring expenses and suggest which categories are fixed or flexible.

Pure functions over ledger rows (no file I/O). Expense rows are grouped by
``(category, subcategory, merchant_key)``; each group is scored on how steady
its charge cadence is, how steady its amount is, and (for monthly bills) how
many of the last months it appears in. The scores give each group a verdict:

* ``fixed`` — regular cadence and an amount that barely moves (rent, a
  subscription);
* ``semi`` — regular cadence but an amount that varies (electricity);
* ``flexible`` — everything else (groceries, restaurants).

``suggest_natures`` rolls the group verdicts up to one suggestion per
``(category, subcategory)``, weighted by spend.
"""

import re
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.models.transaction import Transaction, TransactionType

NATURE_FIXED = "fixed"
NATURE_SEMI = "semi"
NATURE_FLEXIBLE = "flexible"
NATURES: tuple[str, ...] = (NATURE_FIXED, NATURE_SEMI, NATURE_FLEXIBLE)

WINDOW_MONTHS = 12
# Charge periods in days that count as "regular": weekly, fortnightly,
# monthly, bi-monthly, quarterly, yearly.
PERIODS = (7, 14, 30, 60, 91, 365)
MIN_CHARGES = 3  # a yearly charge needs only 2
FIXED_MAX_VARIATION = 0.12  # tolerates the odd price rise
SEMI_MAX_VARIATION = 0.35
# Two charges a year apart prove little; only a near-identical amount counts.
YEARLY_MAX_VARIATION = 0.10
# A subcategory is suggested a nature only when this share of its spend
# agrees; otherwise it is mixed and suggested "semi".
DOMINANT_SHARE = 0.6


@dataclass
class RecurringGroup:
    """One recurring-looking set of charges and how it scored."""

    key: str
    category: str
    subcategory: str
    label: str
    variants: list[str]
    count: int
    months_present: int
    window_months: int  # months it could have appeared in
    period: int | None  # the snapped period in days, ``None`` if irregular
    typical: Decimal  # median charge magnitude
    variation: float  # coefficient of variation of charge magnitudes
    confidence: int  # 0-100
    verdict: str
    spend: Decimal = field(default=Decimal("0"))  # total magnitude in the window


def merchant_key(description: str) -> str:
    """Return a grouping key for ``description``.

    Uppercased, digits and punctuation dropped, tokens that contained a
    digit removed (store numbers, references), first three words kept — so
    "ZABKA Z123 K.1" and "ZABKA ZA9 K.2" group together. Used only for
    grouping here; a transaction's ``description`` is never modified.
    """
    tokens = []
    for raw in description.upper().split():
        if any(ch.isdigit() for ch in raw):
            continue
        word = re.sub(r"[^\w]", "", raw)
        if word:
            tokens.append(word)
    return " ".join(tokens[:3])


def _snap_period(median_gap: float) -> int | None:
    """Return the entry of ``PERIODS`` that ``median_gap`` days is close to."""
    for period in PERIODS:
        if abs(median_gap - period) <= max(3, 0.15 * period):
            return period
    return None


def _score(
    dates: list[date], amounts: list[Decimal], months_present: int, expected: int
) -> tuple[int | None, float, int, str]:
    """Return ``(period, variation, confidence, verdict)`` for one group."""
    values = [float(a) for a in amounts]
    mean = statistics.fmean(values)
    variation = statistics.pstdev(values) / mean if mean else 0.0
    amount_score = max(0.0, 1 - variation / 0.30)

    days = sorted(set(dates))
    gaps = [(b - a).days for a, b in zip(days, days[1:], strict=False)]
    period = _snap_period(statistics.median(gaps)) if gaps else None
    enough = len(days) >= (2 if period == 365 else MIN_CHARGES)
    if period is None or not enough:
        return None, variation, round(100 * 0.15 * amount_score), NATURE_FLEXIBLE

    spread = statistics.pstdev(gaps) if len(gaps) > 1 else 0.0
    regularity = max(0.0, 1 - spread / (0.25 * period))
    if regularity < 0.5:  # gaps too uneven to call it a cadence
        return None, variation, round(100 * 0.15 * amount_score), NATURE_FLEXIBLE
    coverage = min(1.0, months_present / expected) if period <= 31 else 1.0
    confidence = round(100 * (0.4 * regularity + 0.3 * amount_score + 0.3 * coverage))

    if period == 365 and variation > YEARLY_MAX_VARIATION:
        return None, variation, round(100 * 0.15 * amount_score), NATURE_FLEXIBLE
    if regularity >= 0.7 and variation <= FIXED_MAX_VARIATION and coverage >= 0.7:
        verdict = NATURE_FIXED
    elif regularity >= 0.5 and variation <= SEMI_MAX_VARIATION:
        verdict = NATURE_SEMI
    else:
        verdict = NATURE_FLEXIBLE
    return period, variation, confidence, verdict


def detect_recurring(
    transactions: Iterable[Transaction], today: date
) -> list[RecurringGroup]:
    """Return one scored ``RecurringGroup`` per expense group, most confident first.

    Cadence and amount stability use a group's whole history (so a yearly
    charge can be seen); presence and spend cover the last
    ``WINDOW_MONTHS`` months ending at ``today``. Groups with no charge
    in that window are dropped. Transfers and income are ignored.
    """
    expenses = [t for t in transactions if t.type is TransactionType.EXPENSE]
    if not expenses:
        return []
    window_start = today - timedelta(days=365)

    grouped: dict[tuple[str, str, str], list[Transaction]] = defaultdict(list)
    for txn in expenses:
        grouped[(txn.category, txn.subcategory, merchant_key(txn.description))].append(
            txn
        )

    groups = []
    for (category, subcategory, key), txns in grouped.items():
        recent = [t for t in txns if t.date >= window_start]
        if not recent:
            continue
        months = {(t.date.year, t.date.month) for t in recent}
        # Months the group could have appeared in: since its first charge
        # inside the window, so a subscription started 5 months ago is not
        # marked down for the 7 months before it existed.
        first = min(t.date for t in recent)
        elapsed = (today.year - first.year) * 12 + today.month - first.month + 1
        expected = max(1, min(WINDOW_MONTHS, elapsed))
        period, variation, confidence, verdict = _score(
            [t.date for t in txns],
            [abs(t.amount) for t in txns],
            len(months),
            expected,
        )
        variants = sorted({t.description for t in txns})
        groups.append(
            RecurringGroup(
                key=key,
                category=category,
                subcategory=subcategory,
                label=key.title() if key else (variants[0] or "(no description)"),
                variants=variants,
                count=len(txns),
                months_present=len(months),
                window_months=expected,
                period=period,
                typical=statistics.median(abs(t.amount) for t in txns),
                variation=variation,
                confidence=confidence,
                verdict=verdict,
                spend=sum((abs(t.amount) for t in recent), Decimal("0")),
            )
        )
    groups.sort(key=lambda g: (-g.confidence, -g.spend))
    return groups


def suggest_natures(
    groups: Iterable[RecurringGroup],
) -> dict[tuple[str, str], str]:
    """Return a suggested nature per ``(category, subcategory)``.

    Weighted by spend: the verdict holding at least ``DOMINANT_SHARE`` of a
    subcategory's spend wins; a mixed subcategory is suggested ``semi``.
    """
    spend: dict[tuple[str, str], dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )
    for group in groups:
        spend[(group.category, group.subcategory)][group.verdict] += group.spend

    suggestions = {}
    for pair, by_verdict in spend.items():
        total = sum(by_verdict.values(), Decimal("0"))
        if not total:
            continue
        top = max(by_verdict, key=lambda v: by_verdict[v])
        suggestions[pair] = (
            top
            if by_verdict[top] / total >= Decimal(str(DOMINANT_SHARE))
            else NATURE_SEMI
        )
    return suggestions
