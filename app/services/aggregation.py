"""Shared grouping logic for transaction views and reports.

Backs the transactions list's optional month/type grouping
(``grouped_transaction_view``) and Phase 5's reporting views (annual
summary, year drill-down, MoM/YoY comparisons, net worth over time) —
one shared group-by layer per CLAUDE.md, rather than one-off
aggregation logic per view. Pure functions over in-memory data — no
file I/O.
"""

import calendar
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date as date_
from datetime import timedelta
from decimal import Decimal

from app.models.account import Account
from app.models.transaction import Transaction, TransactionType

TYPE_ORDER = [TransactionType.EXPENSE, TransactionType.INCOME, TransactionType.TRANSFER]


@dataclass
class TypeGroup:
    """One type's transactions within a month, with a subtotal.

    For income/expense, ``subtotal`` is the signed sum (so it's directly
    comparable to ``MonthGroup.net_total``). For transfers, a signed sum
    is always zero (every transfer is an equal-and-opposite pair), so
    ``subtotal`` is the total volume moved instead: the sum of absolute
    amounts, halved to avoid double-counting each pair's two legs.
    """

    type: TransactionType
    subtotal: Decimal
    transactions: list[Transaction]


@dataclass
class MonthGroup:
    """One month's transactions, newest first.

    ``transactions`` is always the full, flat, newest-first list for the
    month regardless of ``groups`` — the transactions-list template reads
    ``groups`` when type grouping is on and falls back to ``transactions``
    directly when it's off, so both views come from the same computation.
    """

    key: str
    label: str
    net_total: Decimal
    groups: list[TypeGroup]
    transactions: list[Transaction]


def group_by_month_and_type(transactions: Iterable[Transaction]) -> list[MonthGroup]:
    """Group transactions by month (newest first), then by type within each month.

    Within each month, transactions are sorted newest-first and split into
    income/expense/transfer buckets — only types actually present in that
    month appear. ``net_total`` is income plus expense; transfers move
    money between accounts rather than gaining or losing it, so they're
    excluded from it.
    """
    by_month: dict[str, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        key = f"{transaction.date.year:04d}-{transaction.date.month:02d}"
        by_month[key].append(transaction)

    months = []
    for key in sorted(by_month, reverse=True):
        month_transactions = sorted(by_month[key], key=lambda t: t.date, reverse=True)
        label = month_transactions[0].date.strftime("%B %Y")

        groups = []
        for txn_type in TYPE_ORDER:
            type_transactions = [t for t in month_transactions if t.type == txn_type]
            if not type_transactions:
                continue
            if txn_type == TransactionType.TRANSFER:
                subtotal = (
                    sum((abs(t.amount) for t in type_transactions), Decimal("0")) / 2
                )
            else:
                subtotal = sum((t.amount for t in type_transactions), Decimal("0"))
            groups.append(
                TypeGroup(
                    type=txn_type, subtotal=subtotal, transactions=type_transactions
                )
            )

        net_total = sum(
            (
                t.amount
                for t in month_transactions
                if t.type != TransactionType.TRANSFER
            ),
            Decimal("0"),
        )
        months.append(
            MonthGroup(
                key=key,
                label=label,
                net_total=net_total,
                groups=groups,
                transactions=month_transactions,
            )
        )

    return months


def merge_months(months: Iterable[MonthGroup]) -> list[MonthGroup]:
    """Collapse every month into a single pseudo-month (month grouping off).

    Type subtotals are re-summed across months (a plain sum, since each
    ``TypeGroup.subtotal`` is already the right per-month figure — signed
    for income/expense, volume-moved for transfers — and both are
    associative under addition). ``months`` must already be newest-first,
    as returned by ``group_by_month_and_type``; concatenating in that order
    keeps the merged transaction lists newest-first too, so no re-sort is
    needed. Returns an empty list for an empty ledger, never a list with
    one empty pseudo-month.
    """
    months = list(months)
    if not months:
        return []

    merged_transactions: list[Transaction] = []
    grouped: dict[TransactionType, list[Transaction]] = defaultdict(list)
    subtotals: dict[TransactionType, Decimal] = defaultdict(lambda: Decimal("0"))
    net_total = Decimal("0")

    for month in months:
        merged_transactions.extend(month.transactions)
        net_total += month.net_total
        for group in month.groups:
            grouped[group.type].extend(group.transactions)
            subtotals[group.type] += group.subtotal

    groups = [
        TypeGroup(type=t, subtotal=subtotals[t], transactions=grouped[t])
        for t in TYPE_ORDER
        if t in grouped
    ]

    return [
        MonthGroup(
            key="all",
            label="All transactions",
            net_total=net_total,
            groups=groups,
            transactions=merged_transactions,
        )
    ]


def grouped_transaction_view(
    transactions: Iterable[Transaction], *, by_month: bool, by_type: bool
) -> list[MonthGroup]:
    """Build the transactions-list view for the requested grouping options.

    Always computes the full month-and-type grouping first, then collapses
    whichever dimension is turned off — see ``merge_months`` for why that's
    equivalent to (and cheaper than) re-aggregating from scratch.
    """
    months = group_by_month_and_type(transactions)
    if not by_month:
        months = merge_months(months)
    if not by_type:
        months = [replace(month, groups=[]) for month in months]
    return months


@dataclass
class YearlyTotal:
    """One calendar year's income/expense/net totals, with a YoY delta.

    ``income_total``/``expense_total`` are signed (income positive,
    expense negative — the app's own convention), so ``net_total`` is
    their plain sum — transfers contribute to neither (same reasoning as
    ``group_by_month_and_type``'s ``net_total``: they move money between
    the user's own accounts rather than gaining or losing it).
    ``transfer_volume`` is a separate, purely informational figure — the
    total amount moved via transfers that year, magnitude not signed,
    same "total volume moved" convention as ``TypeGroup.subtotal`` for
    transfers (``sum(abs(amount)) / 2``, halved to avoid double-counting
    each pair's two legs). ``yoy_delta`` compares ``net_total`` against
    the previous calendar year's; ``None`` for the earliest year present
    (nothing to compare against) — never computed against
    ``transfer_volume``, which isn't a gain/loss figure to begin with.
    """

    year: int
    income_total: Decimal
    expense_total: Decimal
    transfer_volume: Decimal
    net_total: Decimal
    yoy_delta: Decimal | None


def yearly_totals_with_yoy(transactions: Iterable[Transaction]) -> list[YearlyTotal]:
    """Return one ``YearlyTotal`` per year with activity, newest first.

    "Activity" now includes a year with transfers but no income/expense
    at all — such a year still gets a row (income/expense/net all zero)
    so its ``transfer_volume`` has somewhere to show; a year that
    genuinely has no transactions of any kind still doesn't appear.
    """
    by_year: dict[int, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        by_year[transaction.date.year].append(transaction)

    results = []
    previous_net: Decimal | None = None
    for year in sorted(by_year):
        year_transactions = by_year[year]
        income_total = sum(
            (t.amount for t in year_transactions if t.type is TransactionType.INCOME),
            Decimal("0"),
        )
        expense_total = sum(
            (t.amount for t in year_transactions if t.type is TransactionType.EXPENSE),
            Decimal("0"),
        )
        transfer_volume = (
            sum(
                (
                    abs(t.amount)
                    for t in year_transactions
                    if t.type is TransactionType.TRANSFER
                ),
                Decimal("0"),
            )
            / 2
        )
        net_total = income_total + expense_total
        yoy_delta = net_total - previous_net if previous_net is not None else None
        results.append(
            YearlyTotal(
                year=year,
                income_total=income_total,
                expense_total=expense_total,
                transfer_volume=transfer_volume,
                net_total=net_total,
                yoy_delta=yoy_delta,
            )
        )
        previous_net = net_total
    return list(reversed(results))


@dataclass
class MonthlyTotal:
    """One month's income/expense/net totals, with a MoM delta.

    ``mom_delta`` compares ``net_total`` against the *chronologically*
    previous month across the whole ledger, not just within one
    calendar year — so January's delta is computed against the prior
    December, not left blank at a year boundary. ``transfer_volume`` is
    purely informational (same "total volume moved" convention as
    ``YearlyTotal``'s) — never folded into ``net_total`` or ``mom_delta``.
    """

    key: str
    label: str
    income_total: Decimal
    expense_total: Decimal
    transfer_volume: Decimal
    net_total: Decimal
    mom_delta: Decimal | None


def monthly_totals_with_mom(transactions: Iterable[Transaction]) -> list[MonthlyTotal]:
    """Return one ``MonthlyTotal`` per month with activity, newest first."""
    months = list(reversed(group_by_month_and_type(transactions)))  # oldest first

    results = []
    previous_net: Decimal | None = None
    for month in months:
        income_total = next(
            (g.subtotal for g in month.groups if g.type is TransactionType.INCOME),
            Decimal("0"),
        )
        expense_total = next(
            (g.subtotal for g in month.groups if g.type is TransactionType.EXPENSE),
            Decimal("0"),
        )
        transfer_volume = next(
            (g.subtotal for g in month.groups if g.type is TransactionType.TRANSFER),
            Decimal("0"),
        )
        mom_delta = month.net_total - previous_net if previous_net is not None else None
        results.append(
            MonthlyTotal(
                key=month.key,
                label=month.label,
                income_total=income_total,
                expense_total=expense_total,
                transfer_volume=transfer_volume,
                net_total=month.net_total,
                mom_delta=mom_delta,
            )
        )
        previous_net = month.net_total
    return list(reversed(results))


@dataclass
class SubcategoryTotal:
    """One subcategory's total spend/income (magnitude, not signed) and row count."""

    name: str
    total: Decimal
    count: int


@dataclass
class CategoryTotal:
    """One category's total plus its subcategory breakdown, with a YoY delta.

    ``total``/``SubcategoryTotal.total`` are magnitudes (``abs(amount)``)
    rather than signed — an expense category's total reading as a
    positive "amount spent" is more useful on a report than a negative
    number here. Rows with a blank ``subcategory`` still count toward
    ``total``/``count`` but don't get their own ``SubcategoryTotal``
    entry. ``count`` is the number of transactions contributing to
    ``total`` (across every subcategory, named or blank) — shown
    alongside the dollar total since a large total from many small
    transactions reads very differently from the same total via one
    big one.
    """

    name: str
    total: Decimal
    count: int
    yoy_delta: Decimal | None
    subcategories: list[SubcategoryTotal]


@dataclass
class _CategoryAccumulator:
    """Mutable running total/count for one category-or-subcategory bucket."""

    total: Decimal = field(default_factory=lambda: Decimal("0"))
    count: int = 0


def _category_totals(
    transactions: Iterable[Transaction],
    txn_type: TransactionType,
    matches_period: Callable[[date_], bool],
) -> dict[str, dict[str, _CategoryAccumulator]]:
    """Return ``{category: {subcategory: accumulator}}`` for one period and type.

    Shared by ``category_breakdown`` (year-scoped: ``matches_period``
    checks ``d.year == year``) and ``category_totals_for_month``
    (month-scoped: ``d.year == year and d.month == month``) — the two
    previously duplicated this whole accumulation loop and differed only
    in their date predicate.
    """
    totals: dict[str, dict[str, _CategoryAccumulator]] = defaultdict(
        lambda: defaultdict(_CategoryAccumulator)
    )
    for transaction in transactions:
        if transaction.type is not txn_type or not matches_period(transaction.date):
            continue
        bucket = totals[transaction.category][transaction.subcategory]
        bucket.total += abs(transaction.amount)
        bucket.count += 1
    return totals


def category_breakdown(
    transactions: Iterable[Transaction], year: int, txn_type: TransactionType
) -> list[CategoryTotal]:
    """Return ``year``'s ``txn_type`` transactions broken down by category.

    Sorted by total descending. ``yoy_delta`` compares each category's
    total against the same category's total the *previous* calendar
    year (``None`` if that category had no activity that year) — the
    PRD's "comparisons ... at the category ... level" requirement.
    Subcategory-level YoY isn't computed (a deliberate simplification,
    see CLAUDE.md) to avoid a second nested comparison pass; category
    level is what's shown. Raises on ``TRANSFER`` — transfers use a
    fixed category outside the managed tree, never a breakdown target.
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers have no category breakdown")

    transactions = list(transactions)
    current = _category_totals(transactions, txn_type, lambda d: d.year == year)
    previous = _category_totals(transactions, txn_type, lambda d: d.year == year - 1)
    previous_category_totals = {
        name: sum((bucket.total for bucket in subs.values()), Decimal("0"))
        for name, subs in previous.items()
    }

    result = []
    for category, subs in current.items():
        total = sum((bucket.total for bucket in subs.values()), Decimal("0"))
        count = sum(bucket.count for bucket in subs.values())
        subcategories = sorted(
            (
                SubcategoryTotal(name=name, total=bucket.total, count=bucket.count)
                for name, bucket in subs.items()
                if name
            ),
            key=lambda s: s.total,
            reverse=True,
        )
        prior_total = previous_category_totals.get(category)
        yoy_delta = total - prior_total if prior_total is not None else None
        result.append(
            CategoryTotal(
                name=category,
                total=total,
                count=count,
                yoy_delta=yoy_delta,
                subcategories=subcategories,
            )
        )
    return sorted(result, key=lambda c: c.total, reverse=True)


def category_totals_all_time(
    transactions: Iterable[Transaction], txn_type: TransactionType
) -> list[CategoryTotal]:
    """Return every category's whole-history total, sorted by total descending.

    The unbounded counterpart to ``category_breakdown`` (one calendar
    year) and ``category_totals_for_month`` (one month) — same shape,
    but with no time bound at all. ``yoy_delta`` is always ``None``:
    there's no "previous all-time period" to compare an all-time total
    against. Raises on ``TRANSFER``, same as ``category_breakdown``.
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers have no category breakdown")

    totals = _category_totals(transactions, txn_type, lambda _d: True)
    result = []
    for category, subs in totals.items():
        total = sum((bucket.total for bucket in subs.values()), Decimal("0"))
        count = sum(bucket.count for bucket in subs.values())
        subcategories = sorted(
            (
                SubcategoryTotal(name=name, total=bucket.total, count=bucket.count)
                for name, bucket in subs.items()
                if name
            ),
            key=lambda s: s.total,
            reverse=True,
        )
        result.append(
            CategoryTotal(
                name=category,
                total=total,
                count=count,
                yoy_delta=None,
                subcategories=subcategories,
            )
        )
    return sorted(result, key=lambda c: c.total, reverse=True)


def category_totals_for_month(
    transactions: Iterable[Transaction],
    year: int,
    month: int,
    txn_type: TransactionType,
) -> list[CategoryTotal]:
    """Return one calendar month's ``txn_type`` transactions broken down by category.

    The month-scoped counterpart to ``category_breakdown`` — same shape
    (sorted by total descending, each category carrying its own
    subcategory breakdown), but for one month instead of a year and with
    no prior-period comparison (``yoy_delta`` is always ``None`` — a
    month drill-down has nothing analogous to "same month last year"
    computed yet). Backs the ``/reports/{year}/{month}`` drill-down's
    spending pie chart and its income/expense-by-category tables. Raises
    on ``TRANSFER``, same as ``category_breakdown``.
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers have no category breakdown")

    totals = _category_totals(
        transactions, txn_type, lambda d: d.year == year and d.month == month
    )
    result = []
    for category, subs in totals.items():
        total = sum((bucket.total for bucket in subs.values()), Decimal("0"))
        count = sum(bucket.count for bucket in subs.values())
        subcategories = sorted(
            (
                SubcategoryTotal(name=name, total=bucket.total, count=bucket.count)
                for name, bucket in subs.items()
                if name
            ),
            key=lambda s: s.total,
            reverse=True,
        )
        result.append(
            CategoryTotal(
                name=category,
                total=total,
                count=count,
                yoy_delta=None,
                subcategories=subcategories,
            )
        )
    return sorted(result, key=lambda c: c.total, reverse=True)


def category_monthly_totals(
    transactions: Iterable[Transaction], year: int, txn_type: TransactionType
) -> dict[str, dict[str, Decimal]]:
    """Return ``{category: {month_key: total}}`` (magnitudes) for one year and type.

    Complements ``category_breakdown``'s single annual total and
    ``monthly_totals_with_mom``'s all-categories-combined monthly total
    with a category-by-month matrix, so a report can show each
    category's month-to-month trend side by side rather than just one
    number per category. A month with no activity for a given category
    is simply absent from that category's dict — the caller decides how
    to fill the gap (``category_breakdown``'s category order plus the
    year's own month keys, typically zero). Raises on ``TRANSFER``, same
    as ``category_breakdown``.
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers have no category breakdown")

    totals: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )
    for transaction in transactions:
        if transaction.type is not txn_type or transaction.date.year != year:
            continue
        month_key = f"{transaction.date.year:04d}-{transaction.date.month:02d}"
        totals[transaction.category][month_key] += abs(transaction.amount)
    return {category: dict(months) for category, months in totals.items()}


def subcategory_monthly_totals(
    transactions: Iterable[Transaction], year: int, txn_type: TransactionType
) -> dict[str, dict[str, dict[str, Decimal]]]:
    """Return ``{category: {subcategory: {month_key: total}}}`` for one year/type.

    The subcategory-level counterpart to ``category_monthly_totals`` —
    needed for a per-subcategory budget-utilization row, since a
    subcategory can carry its own independent budget (see
    ``app.models.category``). A blank ``subcategory`` ("") is included
    under that key too, same as ``category_breakdown``'s treatment of
    subcategory-less rows — callers wanting only *named* subcategories
    skip that key themselves. Raises on ``TRANSFER``, same as
    ``category_breakdown``.
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers have no category breakdown")

    totals: dict[str, dict[str, dict[str, Decimal]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    )
    for transaction in transactions:
        if transaction.type is not txn_type or transaction.date.year != year:
            continue
        month_key = f"{transaction.date.year:04d}-{transaction.date.month:02d}"
        totals[transaction.category][transaction.subcategory][month_key] += abs(
            transaction.amount
        )
    return {
        category: {sub: dict(months) for sub, months in subs.items()}
        for category, subs in totals.items()
    }


@dataclass
class NetWorthPoint:
    """Combined net worth across every account, at one month's end."""

    key: str
    label: str
    value: Decimal


def net_worth_by_month(
    transactions: Iterable[Transaction], accounts: Iterable[Account]
) -> list[NetWorthPoint]:
    """Return one ``NetWorthPoint`` per month with activity, oldest first.

    Net worth is the sum of every account's starting balance plus every
    transaction's signed amount to date. Summed *across all accounts*
    rather than per-account, a transfer's two equal-and-opposite legs
    always cancel out automatically — the combined total only moves on
    real income/expense, so there's no need to special-case transfers
    the way ``group_by_month_and_type``'s ``net_total`` does. Oldest
    first (unlike every other function here) since this feeds a running
    total a line chart draws left-to-right.
    """
    starting_total = sum((a.starting_balance for a in accounts), Decimal("0"))
    months = list(reversed(group_by_month_and_type(transactions)))  # oldest first

    points = []
    running = starting_total
    for month in months:
        running += sum((t.amount for t in month.transactions), Decimal("0"))
        points.append(NetWorthPoint(key=month.key, label=month.label, value=running))
    return points


UNCATEGORIZED = "Uncategorized"  # matches services.importer.DEFAULT_CATEGORY


@dataclass
class DescriptionCount:
    """One still-``Uncategorized`` description, how often it recurs, and its total."""

    description: str
    count: int
    total: Decimal


def top_uncategorized_descriptions(
    transactions: Iterable[Transaction], *, limit: int = 10
) -> list[DescriptionCount]:
    """Return the most frequent still-``Uncategorized`` descriptions, most common first.

    Surfaces good candidates for new auto-categorization rules — a
    description recurring many times uncategorized is worth a rule far
    more than one appearing once, so this ranks by ``count``, not
    ``total`` (a single large uncategorized transfer would otherwise
    crowd out a small but frequent, more rule-worthy merchant). Blank
    descriptions are excluded — they aren't one merchant, just "no
    description," so grouping them together isn't meaningful — and
    transfers never carry a category outside the fixed ``"Transfer"``
    tree (see CLAUDE.md), so they're excluded too. Ties in count keep
    the order their description was first encountered in ``transactions``.
    """
    counts: dict[str, int] = defaultdict(int)
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for transaction in transactions:
        if transaction.type is TransactionType.TRANSFER:
            continue
        if not transaction.description or transaction.category != UNCATEGORIZED:
            continue
        counts[transaction.description] += 1
        totals[transaction.description] += abs(transaction.amount)

    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [
        DescriptionCount(
            description=description, count=count, total=totals[description]
        )
        for description, count in ranked
    ]


def _full_ledger_month_range(
    transactions: list[Transaction], today: date_
) -> list[tuple[str, str]]:
    """Return every ``("YYYY-MM", "Mon YYYY")`` pair spanning the whole ledger.

    From the earliest transaction present (any category, any type — same
    range ``net_worth_by_month`` effectively covers) through the later of
    the latest transaction or ``today``, so the current month always has
    an entry even before anything's been recorded for it yet. Shared by
    ``category_monthly_series`` and ``category_subcategory_monthly_series``
    so both walk the exact same continuous month axis rather than each
    re-deriving it — a stacked-by-subcategory chart needs to line up
    month-for-month with the single-series chart sitting above it.
    """
    all_dates = [t.date for t in transactions]
    start = min(all_dates)
    end = max(max(all_dates), today)

    months = []
    cursor = start.year * 12 + (start.month - 1)
    last = end.year * 12 + (end.month - 1)
    while cursor <= last:
        year, zero_based_month = divmod(cursor, 12)
        month = zero_based_month + 1
        months.append(
            (f"{year:04d}-{month:02d}", f"{calendar.month_abbr[month]} {year}")
        )
        cursor += 1
    return months


@dataclass
class CategoryMonthPoint:
    """One month's total (magnitude) and transaction count for one category."""

    key: str
    label: str
    total: Decimal
    count: int


def category_monthly_series(
    transactions: Iterable[Transaction],
    category: str,
    txn_type: TransactionType,
    *,
    today: date_ | None = None,
) -> list[CategoryMonthPoint]:
    """Return one point per month for ``category``, oldest first, zero-filled.

    Backs the category-detail trend chart (``/reports/category``) — unlike
    ``category_monthly_totals`` (one year, a month with no activity simply
    absent from the dict), this spans the *whole ledger's* month range
    (``_full_ledger_month_range``), not just this category's own active
    months. Zero-filling gaps matters much more here than for a
    whole-ledger view: a single category can easily go quiet for months
    at a time, and a chart that silently skipped those months would
    misleadingly compress the timeline. Raises on ``TRANSFER``, same as
    ``category_breakdown``.
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers have no category breakdown")

    transactions = list(transactions)
    if not transactions:
        return []

    today = today if today is not None else date_.today()
    month_range = _full_ledger_month_range(transactions, today)

    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[str, int] = defaultdict(int)
    for transaction in transactions:
        if transaction.type is not txn_type or transaction.category != category:
            continue
        key = f"{transaction.date.year:04d}-{transaction.date.month:02d}"
        totals[key] += abs(transaction.amount)
        counts[key] += 1

    return [
        CategoryMonthPoint(
            key=key,
            label=label,
            total=totals.get(key, Decimal("0")),
            count=counts.get(key, 0),
        )
        for key, label in month_range
    ]


@dataclass
class SubcategoryMonthPoint:
    """One subcategory's own monthly series, aligned to a shared month axis.

    ``totals`` is positional, one entry per month in the ``months`` list
    ``category_subcategory_monthly_series`` returns alongside it — not
    keyed by month, since every series in the same result shares the
    exact same month axis and a caller (the stacked chart geometry) needs
    to walk them in lockstep.
    """

    name: str
    totals: list[Decimal]


def category_subcategory_monthly_series(
    transactions: Iterable[Transaction],
    category: str,
    txn_type: TransactionType,
    *,
    today: date_ | None = None,
    limit: int = 9,
) -> tuple[list[CategoryMonthPoint], list[SubcategoryMonthPoint]]:
    """Return ``(months, subcategory_series)`` for a stacked-by-subcategory trend.

    ``months`` is the exact same shape ``category_monthly_series`` returns
    (each month's *combined* total/count across every subcategory), so a
    caller can share one x-axis between the plain category chart and this
    stacked one. ``subcategory_series`` has one entry per subcategory
    that ever had activity, sorted by all-time total descending,
    collapsed past ``limit`` into one "Other" entry — same top-N-plus-
    "Other" convention as ``_svg_pie_chart``. A blank subcategory ("" —
    no subcategory set) is folded into "Other" too rather than silently
    dropped, so every dollar in ``months``' own totals is still
    accounted for somewhere in the stack; it's kept out of the ranking
    key itself so a real subcategory can never collide with it. Returns
    ``([], [])`` for no transactions, and ``(months, [])`` when the
    category has activity but none of it is ever subcategorized — the
    caller's cue that there's no per-subcategory chart worth drawing.
    Raises on ``TRANSFER``, same as ``category_breakdown``.
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers have no category breakdown")

    transactions = list(transactions)
    if not transactions:
        return [], []

    today = today if today is not None else date_.today()
    month_range = _full_ledger_month_range(transactions, today)
    month_index = {key: i for i, (key, _) in enumerate(month_range)}
    month_count = len(month_range)

    category_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    category_counts: dict[str, int] = defaultdict(int)
    sub_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    sub_month_totals: dict[str, list[Decimal]] = defaultdict(
        lambda: [Decimal("0")] * month_count
    )
    blank_month_totals = [Decimal("0")] * month_count
    blank_total = Decimal("0")

    for transaction in transactions:
        if transaction.type is not txn_type or transaction.category != category:
            continue
        key = f"{transaction.date.year:04d}-{transaction.date.month:02d}"
        amount = abs(transaction.amount)
        category_totals[key] += amount
        category_counts[key] += 1
        idx = month_index[key]
        if transaction.subcategory:
            sub_totals[transaction.subcategory] += amount
            sub_month_totals[transaction.subcategory][idx] += amount
        else:
            blank_total += amount
            blank_month_totals[idx] += amount

    months = [
        CategoryMonthPoint(
            key=key,
            label=label,
            total=category_totals.get(key, Decimal("0")),
            count=category_counts.get(key, 0),
        )
        for key, label in month_range
    ]

    if not sub_totals:
        return months, []

    ranked = sorted(sub_totals.items(), key=lambda item: item[1], reverse=True)
    top = ranked[:limit]
    overflow = ranked[limit:]

    series = [
        SubcategoryMonthPoint(name=name, totals=sub_month_totals[name])
        for name, _ in top
    ]
    if overflow or blank_total:
        combined = list(blank_month_totals)
        for name, _ in overflow:
            for i, value in enumerate(sub_month_totals[name]):
                combined[i] += value
        series.append(SubcategoryMonthPoint(name="Other", totals=combined))

    return months, series


@dataclass
class CategoryYearTotal:
    """One calendar year's total/count for one category, with a YoY delta."""

    year: int
    total: Decimal
    count: int
    yoy_delta: Decimal | None


def category_yearly_series(
    transactions: Iterable[Transaction], category: str, txn_type: TransactionType
) -> list[CategoryYearTotal]:
    """Return one ``CategoryYearTotal`` per year with activity, newest first.

    The category-scoped counterpart to ``yearly_totals_with_yoy`` — same
    plain total-vs-immediately-prior-year convention (not adjusted for a
    currently in-progress year; the caller decides how to present that,
    same simplification ``yearly_totals_with_yoy`` already accepts).
    Raises on ``TRANSFER``, same as ``category_breakdown``.
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers have no category breakdown")

    totals: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[int, int] = defaultdict(int)
    for transaction in transactions:
        if transaction.type is not txn_type or transaction.category != category:
            continue
        totals[transaction.date.year] += abs(transaction.amount)
        counts[transaction.date.year] += 1

    results = []
    previous_total: Decimal | None = None
    for year in sorted(totals):
        total = totals[year]
        yoy_delta = total - previous_total if previous_total is not None else None
        results.append(
            CategoryYearTotal(
                year=year, total=total, count=counts[year], yoy_delta=yoy_delta
            )
        )
        previous_total = total
    return list(reversed(results))


@dataclass
class SubcategoryShare:
    """One subcategory's total/count and its share of the category's total."""

    name: str
    total: Decimal
    count: int
    pct: float


def category_subcategory_shares(
    transactions: Iterable[Transaction],
    category: str,
    txn_type: TransactionType,
    *,
    year: int | None = None,
    month: int | None = None,
) -> list[SubcategoryShare]:
    """Return ``category``'s subcategory breakdown, sorted by total descending.

    Scoped to one calendar year when ``year`` is given (and further to
    one calendar month within it when ``month`` is also given — ignored
    on its own, same "nothing to anchor a bare month to" treatment as
    ``category_detail``'s own year/month handling), else the category's
    entire history. A blank subcategory (no subcategory set) doesn't get
    its own row — same treatment as ``category_breakdown``'s subcategory
    list — but its amount still counts toward the category total each
    row's ``pct`` is a share of, so the rows shown are honestly allowed
    to add up to less than 100%. Raises on ``TRANSFER``, same as
    ``category_breakdown``.
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers have no category breakdown")

    category_total = Decimal("0")
    sub_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    sub_counts: dict[str, int] = defaultdict(int)
    for transaction in transactions:
        if transaction.type is not txn_type or transaction.category != category:
            continue
        if year is not None and transaction.date.year != year:
            continue
        if year is not None and month is not None and transaction.date.month != month:
            continue
        amount = abs(transaction.amount)
        category_total += amount
        if transaction.subcategory:
            sub_totals[transaction.subcategory] += amount
            sub_counts[transaction.subcategory] += 1

    if category_total == 0:
        return []
    return sorted(
        (
            SubcategoryShare(
                name=name,
                total=total,
                count=sub_counts[name],
                pct=float(total / category_total * 100),
            )
            for name, total in sub_totals.items()
        ),
        key=lambda s: s.total,
        reverse=True,
    )


@dataclass
class CategoryMover:
    """One category's year-to-date total vs. the same months last year.

    Same "compare like-for-like calendar span" convention the category
    detail page's own YTD tile already uses (see
    ``app.routers.reports.category_detail``'s ``ytd_delta``), generalized
    across every category at once — this is what lets the reports
    landing page answer "which categories moved the most this year, in
    dollars" without already having one category in mind. ``prior_total``
    is ``None`` when the category had no activity in the same span last
    year (nothing to compare against, so ``delta``/``pct_delta`` are also
    ``None`` — a brand-new category doesn't get a misleading "infinite"
    percentage).
    """

    name: str
    ytd_total: Decimal
    prior_total: Decimal | None
    delta: Decimal | None
    pct_delta: float | None


def category_movers(
    transactions: Iterable[Transaction], txn_type: TransactionType, today: date_
) -> list[CategoryMover]:
    """Return every category's YTD total vs. the same months last year, ranked by delta.

    Sorted by ``delta`` descending (categories with no comparable
    prior-year data sort last, since there's nothing to rank them by).
    ``today`` is the as-of date (injected, not read internally — same
    convention as ``_full_ledger_month_range``), defining both "year to
    date" and "the same months" cutoff. Raises on ``TRANSFER``, same as
    ``category_breakdown``.
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers have no category breakdown")

    this_year, cutoff_month = today.year, today.month
    ytd_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    prior_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    prior_seen: set[str] = set()
    for transaction in transactions:
        if transaction.type is not txn_type or transaction.date.month > cutoff_month:
            continue
        if transaction.date.year == this_year:
            ytd_totals[transaction.category] += abs(transaction.amount)
        elif transaction.date.year == this_year - 1:
            prior_totals[transaction.category] += abs(transaction.amount)
            prior_seen.add(transaction.category)

    names = set(ytd_totals) | prior_seen
    results = []
    for name in names:
        ytd_total = ytd_totals.get(name, Decimal("0"))
        prior_total = prior_totals[name] if name in prior_seen else None
        delta = ytd_total - prior_total if prior_total is not None else None
        pct_delta = float(delta / prior_total * 100) if prior_total else None
        results.append(
            CategoryMover(
                name=name,
                ytd_total=ytd_total,
                prior_total=prior_total,
                delta=delta,
                pct_delta=pct_delta,
            )
        )
    results.sort(key=lambda m: (m.delta is None, -(m.delta or Decimal("0"))))
    return results


def category_recent_monthly_totals(
    transactions: Iterable[Transaction],
    txn_type: TransactionType,
    today: date_,
    months: int = 12,
) -> tuple[list[str], dict[str, list[Decimal]]]:
    """Return the last ``months`` calendar months' totals per category, aligned.

    ``months`` counts back from (and including) ``today``'s own month,
    oldest first — e.g. 12 months as of March 2026 spans April 2025
    through March 2026. Every category gets a value for every returned
    key (zero-filled), same convention as ``category_monthly_series``, so
    a categories-index sparkline can plot any category on the same axis
    regardless of which months it was actually active in. Raises on
    TRANSFER, same as ``category_breakdown``.
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers have no category breakdown")

    end = today.year * 12 + (today.month - 1)
    start = end - months + 1
    keys = []
    for cursor in range(start, end + 1):
        year, zero_based_month = divmod(cursor, 12)
        keys.append(f"{year:04d}-{zero_based_month + 1:02d}")
    key_set = set(keys)

    totals: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )
    for transaction in transactions:
        if transaction.type is not txn_type:
            continue
        key = f"{transaction.date.year:04d}-{transaction.date.month:02d}"
        if key not in key_set:
            continue
        totals[transaction.category][key] += abs(transaction.amount)

    result = {
        name: [month_totals.get(key, Decimal("0")) for key in keys]
        for name, month_totals in totals.items()
    }
    return keys, result


@dataclass
class CategoryMoM:
    """One category's total vs. its immediately preceding calendar month.

    ``delta`` is signed (a decrease is negative) — same "plain
    total-vs-prior-period" convention as ``CategoryTotal.yoy_delta``, one
    rung down in timescale. ``pct_delta`` is ``delta`` as a percentage of
    the *previous* month's total, magnitude-based (against ``abs``, so a
    swing on an expense category isn't sign-flipped by the category's
    own sign convention) — ``None`` when the previous month had no
    activity at all (a brand-new category, nothing to compute a percent
    change against). ``subcategory_deltas`` maps subcategory name to its
    own signed delta (a blank subcategory is excluded, same as
    ``CategoryTotal.subcategories``) — computing this alongside the
    category-level delta is effectively free (both months' transactions
    are already being walked), unlike the YoY case where subcategory-level
    comparison was deliberately skipped as a second nested pass (see
    ``category_breakdown``'s docstring).
    """

    delta: Decimal
    pct_delta: Decimal | None
    subcategory_deltas: dict[str, Decimal]


def category_mom_deltas(
    transactions: Iterable[Transaction],
    year: int,
    month: int,
    txn_type: TransactionType,
) -> dict[str, CategoryMoM]:
    """Return ``{category: CategoryMoM}`` for one calendar month vs. the one before it.

    Handles the year boundary itself (January's previous month is
    December of the prior year) — same predicate-based reuse of
    ``_category_totals`` as ``category_totals_for_month``. A category
    present in only one of the two months still gets an entry (the
    missing side treated as zero), so a brand-new or fully-dropped
    category still shows up as a real swing rather than being silently
    absent. Raises on ``TRANSFER``, same as ``category_breakdown``.
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers have no category breakdown")

    prev_year, prev_month = (year, month - 1) if month > 1 else (year - 1, 12)
    transactions = list(transactions)
    current = _category_totals(
        transactions, txn_type, lambda d: d.year == year and d.month == month
    )
    previous = _category_totals(
        transactions, txn_type, lambda d: d.year == prev_year and d.month == prev_month
    )

    def _totals(
        buckets: dict[str, dict[str, _CategoryAccumulator]],
    ) -> dict[str, Decimal]:
        return {
            name: sum((bucket.total for bucket in subs.values()), Decimal("0"))
            for name, subs in buckets.items()
        }

    current_totals = _totals(current)
    previous_totals = _totals(previous)
    names = set(current_totals) | set(previous_totals)

    result = {}
    for name in names:
        cur_subs = current.get(name, {})
        prev_subs = previous.get(name, {})
        sub_names = (set(cur_subs) | set(prev_subs)) - {""}
        subcategory_deltas = {
            sub: cur_subs.get(sub, _CategoryAccumulator()).total
            - prev_subs.get(sub, _CategoryAccumulator()).total
            for sub in sub_names
        }
        prev_total = previous_totals.get(name, Decimal("0"))
        delta = current_totals.get(name, Decimal("0")) - prev_total
        result[name] = CategoryMoM(
            delta=delta,
            pct_delta=(delta / abs(prev_total) * 100) if prev_total else None,
            subcategory_deltas=subcategory_deltas,
        )
    return result


def rolling_average_daily_expense(
    transactions: Iterable[Transaction], today: date_, days: int
) -> Decimal | None:
    """Return the trailing ``days``-day average daily expense ending ``today``.

    The window is ``[today - days + 1, today]`` (inclusive on both ends,
    so a 30-day window really spans 30 calendar days). Divided by
    however many of those days actually fall within the ledger's own
    history — from its earliest transaction of any type, same "tracking
    started here" convention ``_full_ledger_month_range`` uses — rather
    than always by ``days``: a ledger only 10 days old would otherwise
    have its 90-day average diluted by 80 days that were never tracked,
    not 80 days of genuine zero spending. Returns ``None`` when the
    ledger has no history at all within the window (an empty ledger, or
    one that only starts after the window ends).
    """
    transactions = list(transactions)
    if not transactions:
        return None

    first_date = min(t.date for t in transactions)
    window_start = today - timedelta(days=days - 1)
    effective_start = max(window_start, first_date)
    if effective_start > today:
        return None
    effective_days = (today - effective_start).days + 1

    total = sum(
        (
            abs(t.amount)
            for t in transactions
            if t.type is TransactionType.EXPENSE and window_start <= t.date <= today
        ),
        Decimal("0"),
    )
    return total / effective_days


# The standard average calendar month length (365.25 / 12) — used only to
# convert a daily rate into a monthly-equivalent one, not to define any
# actual date range (every date-range calculation in this module stays in
# real calendar days).
_AVG_DAYS_PER_MONTH = Decimal("30.44")


def rolling_average_monthly_expense(
    transactions: Iterable[Transaction], today: date_, days: int
) -> Decimal | None:
    """Return the trailing ``days``-day average expense, expressed per month.

    A monthly-equivalent reading of ``rolling_average_daily_expense`` —
    same window and "divide by tracked days, not always the full window"
    handling, just scaled by the average month length instead of shown
    as a raw $/day rate, since "how much am I spending a month" is the
    more natural unit here given this app's budgets/reports are already
    monthly, not daily.
    """
    daily = rolling_average_daily_expense(transactions, today, days)
    return daily * _AVG_DAYS_PER_MONTH if daily is not None else None
