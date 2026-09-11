"""Types for the category → subcategory tree.

Kept as plain ``dict``s rather than pydantic models: the shape is a flat
mapping of category name to a list of subcategory names, with no per-entry
validation beyond that — a model would add ceremony without adding safety.

Income and expense keep entirely separate trees (a "Salary" category makes
no sense on the expense side, and vice versa), so the on-disk/in-memory
shape is one tree per transaction type, keyed by ``TransactionType.value``
("income" / "expense"). Transfers don't participate — they use a fixed
"Transfer" category outside this tree (see ``services.transactions``).
"""

CategoryTree = dict[str, list[str]]
CategoriesByType = dict[str, CategoryTree]
