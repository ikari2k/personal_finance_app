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

from app.models.category import VALID_ICONS, CategoriesByType
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


def _with_tree(
    categories: CategoriesByType, txn_type: TransactionType, tree: dict
) -> CategoriesByType:
    updated = dict(categories)
    updated[txn_type.value] = tree
    return updated


def add_category(
    categories: CategoriesByType, txn_type: TransactionType, name: str, icon: str = ""
) -> CategoriesByType:
    """Return ``categories`` with a new category added to ``txn_type``'s tree.

    Raises ``ValueError`` if ``name`` is blank, already exists in that
    tree, or ``icon`` isn't a known icon key.
    """
    name = name.strip()
    if not name:
        raise ValueError("category name is required")
    tree = _tree(categories, txn_type)
    if name in tree:
        raise ValueError(f"category '{name}' already exists")
    _validate_icon(icon)
    tree[name] = {"icon": icon, "subcategories": {}}
    return _with_tree(categories, txn_type, tree)


def update_category(
    categories: CategoriesByType,
    txn_type: TransactionType,
    current_name: str,
    *,
    name: str,
    icon: str = "",
) -> CategoriesByType:
    """Return ``categories`` with ``current_name`` renamed/re-iconed.

    Keeps ``current_name``'s subcategories under the new name. Raises
    ``ValueError`` if ``current_name`` doesn't exist, ``name`` is blank, a
    *different* category already uses ``name``, or ``icon`` isn't a known
    icon key.
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
) -> CategoriesByType:
    """Return ``categories`` with a new subcategory added under ``category_name``.

    Raises ``ValueError`` if ``category_name`` doesn't exist, ``name`` is
    blank or already exists under that category, or ``icon`` isn't a known
    icon key.
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
    subcategories[name] = icon
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
) -> CategoriesByType:
    """Return ``categories`` with a subcategory renamed/re-iconed.

    Raises ``ValueError`` if ``category_name`` or ``current_name`` doesn't
    exist, ``name`` is blank, a *different* subcategory already uses
    ``name``, or ``icon`` isn't a known icon key.
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
    subcategories[name] = icon
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
