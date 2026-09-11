"""Type alias for the category → subcategory tree.

Kept as a plain ``dict`` rather than a pydantic model: the shape is a flat
mapping of category name to a list of subcategory names, with no per-entry
validation beyond that — a model would add ceremony without adding safety.
"""

CategoryTree = dict[str, list[str]]
