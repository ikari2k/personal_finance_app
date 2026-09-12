"""Shared grouping logic for transaction views and (later) reports.

Currently backs the transactions list's optional month/type grouping.
Phase 5 reporting is expected to extend this module with
category/subcategory grouping rather than duplicating a second group-by
layer. Pure functions over in-memory data — no file I/O.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal

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
