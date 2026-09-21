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

Both a category and each of its subcategories also carry an optional
monthly budget (``""`` means "no budget set", else a quoted non-negative
decimal string — same TOML-quoting convention as ``Account
.starting_balance``, kept as a plain ``str`` here rather than
``Decimal`` since these are TypedDicts, not pydantic models). A
category's budget and its subcategories' budgets are independent
thresholds, not a hierarchy: the category's budget is checked against
total spend across *all* of its transactions that month (every
subcategory plus any transaction with no subcategory), while each
subcategory's budget is checked only against that subcategory's own
spend. There is deliberately no validation that subcategory budgets sum
to (or stay under) the category budget — that's what lets "category
only", "some subcategories only", "all subcategories", and "both levels
at once" all be valid simultaneously without special-casing. Budget
validation (non-negative) lives in ``services.categories``; the
monthly spend-vs-budget comparison itself is a future phase (a
dedicated Monthly Budgets page), not implemented yet.
"""

from typing import TypedDict


class SubcategoryEntry(TypedDict):
    """One subcategory: its icon and its own optional monthly budget."""

    icon: str
    budget: str


class CategoryEntry(TypedDict):
    """One category.

    Carries its icon, its own optional monthly budget, and its
    subcategory → ``SubcategoryEntry`` mapping.
    """

    icon: str
    budget: str
    subcategories: dict[str, SubcategoryEntry]


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
        "fuel",
        "parking",
        "dog",
        "disc",
        "comic",
        "apple",
        "baby",
        "star",
        "ferris-wheel",
        "backpack",
        "blocks",
        "person",
        "scissors",
        "lipstick",
        "donate",
        "cocktail",
        "pill",
        "stethoscope",
        "bus",
        "train",
        "armchair",
        "flower",
        "zap",
        "droplet",
        "library",
        "hotel",
        "cat",
        "fish",
        "key",
        "users",
        "cloud",
        "clapperboard",
        "credit-card",
        "wallet",
        "taxi",
        "luggage",
        "spade",
        "truck",
    }
)

# Short "what's this icon for" hints shown as a tooltip on each picker-grid
# choice (categories/_form.html) — a bare glyph name ("mask", "dice") isn't
# always obvious at a glance. Must cover every key in VALID_ICONS exactly;
# checked in tests/unit/test_categories_icon_data.py rather than asserted
# at import time, to keep this module free of side effects at import.
ICON_HINTS: dict[str, str] = {
    "cart": "Groceries, shopping",
    "home": "Housing, rent, mortgage",
    "car": "Car, transportation",
    "film": "Movies, cinema",
    "heart": "Health, charity",
    "shirt": "Clothing",
    "briefcase": "Work, salary",
    "laptop": "Freelance, tech, electronics",
    "bulb": "Utilities, electricity",
    "store": "Retail, supermarket",
    "utensils": "Dining out, restaurants",
    "tv": "Streaming, entertainment",
    "bank": "Bank, government, institution",
    "plane": "Travel, flights",
    "cap": "Education, tuition",
    "gift": "Gifts",
    "phone": "Phone bill, mobile",
    "wrench": "Maintenance, repairs",
    "piggy-bank": "Savings",
    "receipt": "Bills, taxes",
    "dumbbell": "Fitness, gym",
    "coffee": "Coffee, cafes",
    "music": "Music, concerts",
    "book": "Books, reading",
    "banknote": "Salary, cash income",
    "refresh": "Refunds, reimbursements",
    "shield": "Insurance",
    "x": "Other, miscellaneous",
    "arrow-up-right": "Advance, investment out",
    "trending-up": "Account reconciliation, investment",
    "sun": "Recharge, energy top-up",
    "message": "Communication, chat",
    "mail": "Mail, postal, courier",
    "wifi": "Internet",
    "globe": "Web services, international",
    "megaphone": "Marketing, announcements",
    "headphones": "Music, audio, podcasts",
    "camera": "Photography",
    "ticket": "Events, concerts, tickets",
    "mask": "Theater, entertainment",
    "gamepad": "Games, gaming",
    "dice": "Games, gambling",
    "fuel": "Fuel, gas",
    "parking": "Parking",
    "dog": "Pets, veterinary",
    "disc": "Movies, music, physical media (CD/Blu-ray)",
    "comic": "Comics, books, entertainment",
    "apple": "Fresh produce, fruit",
    "baby": "Kids, childcare",
    "star": "Activities, achievements",
    "ferris-wheel": "Attractions, amusement park",
    "backpack": "School, education",
    "blocks": "Toys, kids",
    "person": "Personal",
    "scissors": "Haircut, barber, salon",
    "lipstick": "Cosmetics, makeup, beauty",
    "donate": "Donations, charity giving",
    "cocktail": "Drinks, bar, nightlife",
    "pill": "Medicine, pharmacy",
    "stethoscope": "Doctor, medical checkup",
    "bus": "Bus, public transit",
    "train": "Train",
    "armchair": "Furniture",
    "flower": "Garden, plants",
    "zap": "Electricity",
    "droplet": "Water utility",
    "library": "Library, library fees",
    "hotel": "Hotel, accommodation",
    "cat": "Pets, cats",
    "fish": "Pets, fish",
    "key": "Rental deposit, access",
    "users": "Family, spouse, shared expense",
    "cloud": "Cloud storage, backups",
    "clapperboard": "Streaming, movies",
    "credit-card": "Card payment, fees",
    "wallet": "Wallet, cash",
    "taxi": "Taxi, rideshare",
    "luggage": "Travel, luggage",
    "spade": "Card games, tabletop games",
    "truck": "Delivery, shipping",
}
