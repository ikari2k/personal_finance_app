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

from app.models.category import VALID_ICONS, CategoriesByType, CategoryEntry
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


def add_category(
    categories: CategoriesByType,
    txn_type: TransactionType,
    name: str,
    icon: str = "",
    budget: Decimal | None = None,
) -> CategoriesByType:
    """Return ``categories`` with a new category added to ``txn_type``'s tree.

    Raises ``ValueError`` if ``name`` is blank, already exists in that
    tree, ``icon`` isn't a known icon key, or ``budget`` is negative.
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
) -> CategoriesByType:
    """Return ``categories`` with ``current_name`` renamed/re-iconed/re-budgeted.

    Keeps ``current_name``'s subcategories under the new name. Raises
    ``ValueError`` if ``current_name`` doesn't exist, ``name`` is blank, a
    *different* category already uses ``name``, ``icon`` isn't a known
    icon key, or ``budget`` is negative.
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
) -> CategoriesByType:
    """Return ``categories`` with a new subcategory added under ``category_name``.

    Raises ``ValueError`` if ``category_name`` doesn't exist, ``name`` is
    blank or already exists under that category, ``icon`` isn't a known
    icon key, or ``budget`` is negative.
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
    subcategories[name] = {"icon": icon, "budget": _budget_str(txn_type, budget)}
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
) -> CategoriesByType:
    """Return ``categories`` with a subcategory renamed/re-iconed/re-budgeted.

    Raises ``ValueError`` if ``category_name`` or ``current_name`` doesn't
    exist, ``name`` is blank, a *different* subcategory already uses
    ``name``, ``icon`` isn't a known icon key, or ``budget`` is negative.
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
    del subcategories[current_name]
    subcategories[name] = {"icon": icon, "budget": _budget_str(txn_type, budget)}
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
