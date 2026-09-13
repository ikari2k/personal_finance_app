"""Types for the category → subcategory tree.

Kept as plain ``dict``s rather than pydantic models: the shape has no
per-entry validation beyond icon-key membership (checked in
``services.categories``), so a model would add ceremony without adding
safety.

Income and expense keep entirely separate trees (a "Salary" category makes
no sense on the expense side, and vice versa), so the on-disk/in-memory
shape is one tree per transaction type, keyed by ``TransactionType.value``
("income" / "expense"). Transfers don't participate — they use a fixed
"Transfer" category outside this tree (see ``services.transactions``).

Each category carries an optional icon (a key into the vendored icon set
rendered by ``app/templates/_category_icons.html``'s ``category_icon()``
macro) and its own subcategories, each of which carries its own optional
icon. ``""`` means "no icon assigned" rather than a missing key or
``None`` — TOML has no null, and this matches the rest of the app's
convention of an empty string for "unset" (e.g. ``Transaction.subcategory``).
"""

from typing import TypedDict


class CategoryEntry(TypedDict):
    """One category: its icon and its subcategory → icon mapping."""

    icon: str
    subcategories: dict[str, str]


CategoryTree = dict[str, CategoryEntry]
CategoriesByType = dict[str, CategoryTree]

# The vendored stroke-icon set (app/templates/_category_icons.html) — every
# key there must have a matching macro in that file's icon registry, and
# every key here must appear in the picker grid template. Kept here, not
# there, so services.categories can validate a submitted icon key without
# round-tripping through Jinja.
VALID_ICONS: frozenset[str] = frozenset(
    {
        "cart",
        "home",
        "car",
        "film",
        "heart",
        "shirt",
        "briefcase",
        "laptop",
        "bulb",
        "store",
        "utensils",
        "tv",
        "bank",
        "plane",
        "cap",
        "gift",
        "phone",
        "wrench",
        "piggy-bank",
        "receipt",
        "dumbbell",
        "coffee",
        "music",
        "book",
        "banknote",
        "refresh",
        "shield",
        "x",
        "arrow-up-right",
        "trending-up",
        "sun",
        "message",
        "mail",
        "wifi",
        "globe",
        "megaphone",
        "headphones",
        "camera",
        "ticket",
        "mask",
        "gamepad",
        "dice",
    }
)
