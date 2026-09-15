"""Shared grouping logic for transaction views and reports.

Backs the transactions list's optional month/type grouping
(``grouped_transaction_view``) and Phase 5's reporting views (annual
summary, year drill-down, MoM/YoY comparisons, net worth over time) —
one shared group-by layer per CLAUDE.md, rather than one-off
aggregation logic per view. Pure functions over in-memory data — no
file I/O.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
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
    their plain sum. ``yoy_delta`` compares ``net_total`` against the
    previous calendar year's; ``None`` for the earliest year present
    (nothing to compare against).
    """

    year: int
    income_total: Decimal
    expense_total: Decimal
    net_total: Decimal
    yoy_delta: Decimal | None


def yearly_totals_with_yoy(transactions: Iterable[Transaction]) -> list[YearlyTotal]:
    """Return one ``YearlyTotal`` per year with activity, newest first.

    Transfers are excluded — they move money between the user's own
    accounts rather than gaining or losing it (same reasoning as
    ``group_by_month_and_type``'s ``net_total``).
    """
    by_year: dict[int, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        if transaction.type is not TransactionType.TRANSFER:
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
        net_total = income_total + expense_total
        yoy_delta = net_total - previous_net if previous_net is not None else None
        results.append(
            YearlyTotal(
                year=year,
                income_total=income_total,
                expense_total=expense_total,
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
    December, not left blank at a year boundary.
    """

    key: str
    label: str
    income_total: Decimal
    expense_total: Decimal
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
        mom_delta = month.net_total - previous_net if previous_net is not None else None
        results.append(
            MonthlyTotal(
                key=month.key,
                label=month.label,
                income_total=income_total,
                expense_total=expense_total,
                net_total=month.net_total,
                mom_delta=mom_delta,
            )
        )
        previous_net = month.net_total
    return list(reversed(results))


@dataclass
class SubcategoryTotal:
    """One subcategory's total spend/income (magnitude, not signed)."""

    name: str
    total: Decimal


@dataclass
class CategoryTotal:
    """One category's total plus its subcategory breakdown, with a YoY delta.

    ``total``/``SubcategoryTotal.total`` are magnitudes (``abs(amount)``)
    rather than signed — an expense category's total reading as a
    positive "amount spent" is more useful on a report than a negative
    number here. Rows with a blank ``subcategory`` still count toward
    ``total`` but don't get their own ``SubcategoryTotal`` entry.
    """

    name: str
    total: Decimal
    yoy_delta: Decimal | None
    subcategories: list[SubcategoryTotal]


def _category_totals_for_year(
    transactions: Iterable[Transaction], year: int, txn_type: TransactionType
) -> dict[str, dict[str, Decimal]]:
    """Return ``{category: {subcategory: total}}`` for one year and type."""
    totals: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )
    for transaction in transactions:
        if transaction.type is not txn_type or transaction.date.year != year:
            continue
        totals[transaction.category][transaction.subcategory] += abs(transaction.amount)
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
    current = _category_totals_for_year(transactions, year, txn_type)
    previous = _category_totals_for_year(transactions, year - 1, txn_type)
    previous_category_totals = {
        name: sum(subs.values(), Decimal("0")) for name, subs in previous.items()
    }

    result = []
    for category, subs in current.items():
        total = sum(subs.values(), Decimal("0"))
        subcategories = sorted(
            (
                SubcategoryTotal(name=name, total=amount)
                for name, amount in subs.items()
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
                yoy_delta=yoy_delta,
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
