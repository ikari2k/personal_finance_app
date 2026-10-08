"""Business rules for managing the category/subcategory tree.

Pure functions over in-memory data — no file I/O, matching
``services.transactions``. Callers (routers) read the current tree via
``app.storage.categories``, call these to validate and build the updated
tree, and write the result back afterward.

Deletion (of either a category or a subcategory) is deliberately **not**
guarded by ledger usage — a ledger row's ``category``/``subcategory`` are
free-text strings copied in at entry time (see
``services.transactions.ensure_category``), not a foreign key the
consistency checker validates the way ``account_id`` is. Renaming or
deleting a tree entry never touches existing ledger rows, consistent with
the PRD's "no automatic dedup/cleanup" stance. Don't add a usage guard
here by analogy with ``services.accounts.remove_account``; they aren't the
same kind of reference.
"""

from decimal import Decimal

from app.models.category import (
    EXPENSE_BUCKETS,
    INCOME_BUCKETS,
    VALID_ICONS,
    CategoriesByType,
    CategoryEntry,
)
from app.models.transaction import TransactionType


def _tree(categories: CategoriesByType, txn_type: TransactionType) -> dict:
    """Validate ``txn_type`` and return a shallow copy of its tree's dict.

    Raises ``ValueError`` for ``TRANSFER`` — it has no managed category
    tree (see ``app.models.category``).
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers do not use the managed category tree")
    return dict(categories.get(txn_type.value, {}))


def _validate_icon(icon: str) -> None:
    if icon and icon not in VALID_ICONS:
        raise ValueError(f"unknown icon '{icon}'")


def _validate_bucket(txn_type: TransactionType, bucket: str, *, sub: bool) -> str:
    """Validate a 50/30/20 ``bucket`` for ``txn_type`` and return it.

    Expense entries accept ``""``/``"need"``/``"want"``; income categories
    accept ``""``/``"excluded"``. Income subcategories have no bucket of
    their own, so only ``""`` is valid for them. Raises ``ValueError``
    otherwise.
    """
    allowed = EXPENSE_BUCKETS if txn_type is TransactionType.EXPENSE else INCOME_BUCKETS
    if sub and txn_type is TransactionType.INCOME:
        allowed = frozenset({""})
    if bucket not in allowed:
        raise ValueError(f"invalid bucket '{bucket}'")
    return bucket


def _budget_str(txn_type: TransactionType, budget: Decimal | None) -> str:
    """Validate and serialize a monthly budget to the on-disk ``str`` form.

    ``None`` means "no budget set" (stored as ``""``). Raises
    ``ValueError`` if ``budget`` is negative, or if it's set at all for an
    income category/subcategory — budgets exist to flag overspending, which
    isn't a meaningful concept for income.
    """
    if budget is None:
        return ""
    if txn_type is TransactionType.INCOME:
        raise ValueError("income categories cannot have a monthly budget")
    if budget < 0:
        raise ValueError("budget must not be negative")
    return str(budget)


def _with_tree(
    categories: CategoriesByType, txn_type: TransactionType, tree: dict
) -> CategoriesByType:
    updated = dict(categories)
    updated[txn_type.value] = tree
    return updated


def subcategories_exceed_category_budget(entry: CategoryEntry) -> bool:
    """Return whether ``entry``'s subcategory budgets add up to more than its own.

    Category and subcategory budgets are independent thresholds (see
    ``app.models.category``) — this is deliberately not enforced as a
    save-time validation error, since either combination is a legitimate
    setup. It's surfaced instead as a non-blocking warning on the
    category listing, so an "optimistic" setup (subcategory budgets that
    only work out if not everyone maxes out at once) is a visible choice
    rather than a silent one.
    """
    if not entry["budget"]:
        return False
    subcategory_total = sum(
        (
            Decimal(sub["budget"])
            for sub in entry["subcategories"].values()
            if sub["budget"]
        ),
        start=Decimal(0),
    )
    return subcategory_total > Decimal(entry["budget"])


def category_pair_exists(
    categories: CategoriesByType,
    txn_type: TransactionType,
    category: str,
    subcategory: str,
) -> bool:
    """Return whether ``category``/``subcategory`` already exist in ``txn_type``'s tree.

    Backs the transactions list's inline category-picker (``app.routers
    .transactions.update_transaction_category_route``), which — unlike
    every other category-entry point in the app — deliberately never
    creates a new category/subcategory on the fly; the picker's own
    ``<select>`` only ever offers existing ones, but the server still
    checks rather than trusting the client. A blank ``subcategory``
    means "just the bare category," which is valid whenever ``category``
    itself exists.
    """
    entry = categories.get(txn_type.value, {}).get(category)
    if entry is None:
        return False
    if not subcategory:
        return True
    return subcategory in entry["subcategories"]


def add_category(
    categories: CategoriesByType,
    txn_type: TransactionType,
    name: str,
    icon: str = "",
    budget: Decimal | None = None,
    bucket: str = "",
) -> CategoriesByType:
    """Return ``categories`` with a new category added to ``txn_type``'s tree.

    Raises ``ValueError`` if ``name`` is blank, already exists in that
    tree, ``icon`` isn't a known icon key, ``budget`` is negative, or
    ``bucket`` isn't valid for ``txn_type``.
    """
    name = name.strip()
    if not name:
        raise ValueError("category name is required")
    tree = _tree(categories, txn_type)
    if name in tree:
        raise ValueError(f"category '{name}' already exists")
    _validate_icon(icon)
    tree[name] = {
        "icon": icon,
        "budget": _budget_str(txn_type, budget),
        "bucket": _validate_bucket(txn_type, bucket, sub=False),
        "subcategories": {},
    }
    return _with_tree(categories, txn_type, tree)


def update_category(
    categories: CategoriesByType,
    txn_type: TransactionType,
    current_name: str,
    *,
    name: str,
    icon: str = "",
    budget: Decimal | None = None,
    bucket: str | None = None,
) -> CategoriesByType:
    """Return ``categories`` with ``current_name`` renamed/re-iconed/re-budgeted.

    Keeps ``current_name``'s subcategories under the new name. Unlike
    ``budget``, omitting ``bucket`` (``None``) keeps the current value.
    Raises ``ValueError`` if ``current_name`` doesn't exist, ``name`` is blank, a
    *different* category already uses ``name``, ``icon`` isn't a known
    icon key, ``budget`` is negative, or ``bucket`` isn't valid.
    """
    name = name.strip()
    if not name:
        raise ValueError("category name is required")
    tree = _tree(categories, txn_type)
    if current_name not in tree:
        raise ValueError(f"no category '{current_name}'")
    if name != current_name and name in tree:
        raise ValueError(f"category '{name}' already exists")
    _validate_icon(icon)
    entry = tree.pop(current_name)
    entry["icon"] = icon
    entry["budget"] = _budget_str(txn_type, budget)
    if bucket is not None:
        entry["bucket"] = _validate_bucket(txn_type, bucket, sub=False)
    tree[name] = entry
    return _with_tree(categories, txn_type, tree)


def delete_category(
    categories: CategoriesByType, txn_type: TransactionType, name: str
) -> CategoriesByType:
    """Return ``categories`` with ``name`` (and all its subcategories) removed.

    Raises ``ValueError`` if ``name`` doesn't exist. See the module
    docstring for why this isn't guarded by ledger usage.
    """
    tree = _tree(categories, txn_type)
    if name not in tree:
        raise ValueError(f"no category '{name}'")
    del tree[name]
    return _with_tree(categories, txn_type, tree)


def add_subcategory(
    categories: CategoriesByType,
    txn_type: TransactionType,
    category_name: str,
    name: str,
    icon: str = "",
    budget: Decimal | None = None,
    bucket: str = "",
) -> CategoriesByType:
    """Return ``categories`` with a new subcategory added under ``category_name``.

    Raises ``ValueError`` if ``category_name`` doesn't exist, ``name`` is
    blank or already exists under that category, ``icon`` isn't a known
    icon key, ``budget`` is negative, or ``bucket`` isn't valid.
    """
    name = name.strip()
    if not name:
        raise ValueError("subcategory name is required")
    tree = _tree(categories, txn_type)
    if category_name not in tree:
        raise ValueError(f"no category '{category_name}'")
    subcategories = dict(tree[category_name]["subcategories"])
    if name in subcategories:
        raise ValueError(f"subcategory '{name}' already exists")
    _validate_icon(icon)
    subcategories[name] = {
        "icon": icon,
        "budget": _budget_str(txn_type, budget),
        "bucket": _validate_bucket(txn_type, bucket, sub=True),
    }
    tree[category_name] = {**tree[category_name], "subcategories": subcategories}
    return _with_tree(categories, txn_type, tree)


def update_subcategory(
    categories: CategoriesByType,
    txn_type: TransactionType,
    category_name: str,
    current_name: str,
    *,
    name: str,
    icon: str = "",
    budget: Decimal | None = None,
    bucket: str | None = None,
) -> CategoriesByType:
    """Return ``categories`` with a subcategory renamed/re-iconed/re-budgeted.

    Unlike ``budget``, omitting ``bucket`` (``None``) keeps the current value.

    Raises ``ValueError`` if ``category_name`` or ``current_name`` doesn't
    exist, ``name`` is blank, a *different* subcategory already uses
    ``name``, ``icon`` isn't a known icon key, ``budget`` is negative, or
    ``bucket`` isn't valid.
    """
    name = name.strip()
    if not name:
        raise ValueError("subcategory name is required")
    tree = _tree(categories, txn_type)
    if category_name not in tree:
        raise ValueError(f"no category '{category_name}'")
    subcategories = dict(tree[category_name]["subcategories"])
    if current_name not in subcategories:
        raise ValueError(f"no subcategory '{current_name}'")
    if name != current_name and name in subcategories:
        raise ValueError(f"subcategory '{name}' already exists")
    _validate_icon(icon)
    current_bucket = subcategories.pop(current_name).get("bucket", "")
    subcategories[name] = {
        "icon": icon,
        "budget": _budget_str(txn_type, budget),
        "bucket": (
            current_bucket
            if bucket is None
            else _validate_bucket(txn_type, bucket, sub=True)
        ),
    }
    tree[category_name] = {**tree[category_name], "subcategories": subcategories}
    return _with_tree(categories, txn_type, tree)


def delete_subcategory(
    categories: CategoriesByType,
    txn_type: TransactionType,
    category_name: str,
    name: str,
) -> CategoriesByType:
    """Return ``categories`` with a subcategory removed from ``category_name``.

    Raises ``ValueError`` if ``category_name`` or ``name`` doesn't exist.
    See the module docstring for why this isn't guarded by ledger usage.
    """
    tree = _tree(categories, txn_type)
    if category_name not in tree:
        raise ValueError(f"no category '{category_name}'")
    subcategories = dict(tree[category_name]["subcategories"])
    if name not in subcategories:
        raise ValueError(f"no subcategory '{name}'")
    del subcategories[name]
    tree[category_name] = {**tree[category_name], "subcategories": subcategories}
    return _with_tree(categories, txn_type, tree)


def effective_bucket(entry: CategoryEntry, subcategory: str = "") -> str:
    """Return the 50/30/20 bucket a (category, subcategory) pair falls under.

    A subcategory's own non-empty bucket wins; otherwise it inherits its
    category's. A blank or unknown ``subcategory`` resolves to the
    category's own bucket. ``""`` means unclassified (expense) or counted
    (income).
    """
    sub = entry["subcategories"].get(subcategory)
    if sub and sub.get("bucket"):
        return sub["bucket"]
    return entry.get("bucket", "")


def count_unclassified_expense_categories(categories: CategoriesByType) -> int:
    """Count expense categories with no bucket, for the "N to classify" nudge."""
    return sum(
        1 for entry in categories.get("expense", {}).values() if not entry["bucket"]
    )


def set_buckets(
    categories: CategoriesByType,
    assignments: list[tuple[TransactionType, str, str, str]],
) -> CategoriesByType:
    """Return ``categories`` with many buckets set at once.

    Each assignment is ``(txn_type, category, subcategory, bucket)``; a
    blank ``subcategory`` targets the category itself. Nothing is applied
    unless every assignment is valid. Raises ``ValueError`` for an
    unknown category/subcategory or a bucket invalid for ``txn_type``.
    """
    result = categories
    for txn_type, category, subcategory, bucket in assignments:
        entry = _tree(result, txn_type).get(category)
        if entry is None:
            raise ValueError(f"no category '{category}'")
        if subcategory:
            if subcategory not in entry["subcategories"]:
                raise ValueError(f"no subcategory '{subcategory}'")
            result = update_subcategory(
                result,
                txn_type,
                category,
                subcategory,
                name=subcategory,
                icon=entry["subcategories"][subcategory]["icon"],
                budget=_current_budget(entry["subcategories"][subcategory]["budget"]),
                bucket=bucket,
            )
        else:
            result = update_category(
                result,
                txn_type,
                category,
                name=category,
                icon=entry["icon"],
                budget=_current_budget(entry["budget"]),
                bucket=bucket,
            )
    return result


def _current_budget(budget: str) -> Decimal | None:
    """Turn a stored budget string back into the ``Decimal | None`` updates take."""
    return Decimal(budget) if budget else None
