"""Routes for managing the income/expense category and subcategory trees."""

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.models.category import ICON_HINTS, VALID_ICONS, CategoriesByType
from app.models.transaction import TransactionType
from app.routers.htmx_events import toast
from app.services.aggregation import top_uncategorized_descriptions
from app.services.categories import (
    add_category,
    add_subcategory,
    delete_category,
    delete_subcategory,
    subcategories_exceed_category_budget,
    update_category,
    update_subcategory,
)
from app.services.categorizer import (
    rename_category_in_rules,
    rename_subcategory_in_rules,
)
from app.storage.categories import read_categories, write_categories
from app.storage.ledger import read_ledger
from app.storage.rules import read_rules, write_rules
from app.templating import templates

router = APIRouter(prefix="/categories", tags=["categories"])

ICON_KEYS = sorted(VALID_ICONS)


def _budget_warnings(categories: CategoriesByType) -> dict[str, dict[str, bool]]:
    """Flag, per category, whether its subcategory budgets exceed its own.

    A non-blocking display concern (see
    ``services.categories.subcategories_exceed_category_budget``), computed
    once here so ``_tree.html`` stays presentational.
    """
    return {
        bucket: {
            name: subcategories_exceed_category_budget(entry)
            for name, entry in tree.items()
        }
        for bucket, tree in categories.items()
    }


def _parse_budget(raw: str) -> Decimal | None:
    """Parse a raw budget form field; a blank string means "no budget set".

    Raises ``ValueError`` (not ``InvalidOperation``) on unparseable input,
    so callers can fold it into the same ``except ValueError`` block that
    already handles every other category-form validation error.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("invalid budget amount") from exc


def _render_tree(
    request: Request,
    *,
    oob: bool = False,
    error: str | None = None,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    """Render the two-column income/expense category tree fragment.

    ``oob=True`` marks the fragment as an out-of-band swap target (used to
    refresh the tree from the category/subcategory dialogs, whose own
    response targets the dialog's content area instead).
    """
    categories = read_categories()
    return templates.TemplateResponse(
        request,
        "categories/_tree.html",
        {
            "categories": categories,
            "budget_warnings": _budget_warnings(categories),
            "oob": oob,
            "error": error,
        },
        headers=headers,
    )


def _toast_with_rule_count(base_message: str, rules_renamed: int) -> dict[str, str]:
    """Build the success toast, noting how many rules were kept in sync, if any.

    Silent otherwise — a rename that didn't touch any rule (the common
    case) shouldn't call attention to the fact.
    """
    if not rules_renamed:
        return toast(base_message, close_dialog=True)
    plural = "" if rules_renamed == 1 else "s"
    message = f"{base_message} ({rules_renamed} rule{plural} updated)"
    return toast(message, close_dialog=True)


def _rename_in_rules(old_name: str, new_name: str, apply_rename) -> int:
    """Propagate a category/subcategory rename into ``config/rules.toml``.

    ``apply_rename`` is a ``rules -> renamed_rules`` callable (typically a
    closure over ``rename_category_in_rules``/``rename_subcategory_in_rules``
    with the rest of their args already bound) — shared by both the
    category and subcategory rename routes, which previously duplicated
    this whole read/compare/write block verbatim. A no-op rename
    (``old_name == new_name``) skips the read entirely. Returns how many
    rules actually changed, for the success toast.
    """
    if old_name == new_name:
        return 0
    rules = read_rules()
    renamed_rules = apply_rename(rules)
    if renamed_rules == rules:
        return 0
    write_rules(renamed_rules)
    return sum(1 for old, new in zip(rules, renamed_rules) if old != new)


def _render_form(
    request: Request,
    *,
    dialog_id: str,
    dialog_content_id: str,
    kind: str,
    action_url: str,
    values: dict[str, str],
    editing: bool,
    show_budget: bool,
    error: str | None = None,
) -> HTMLResponse:
    """Render the shared category/subcategory add-or-edit form fragment."""
    return templates.TemplateResponse(
        request,
        "categories/_form.html",
        {
            "dialog_id": dialog_id,
            "dialog_content_id": dialog_content_id,
            "kind": kind,
            "action_url": action_url,
            "values": values,
            "editing": editing,
            "show_budget": show_budget,
            "icon_keys": ICON_KEYS,
            "icon_hints": ICON_HINTS,
            "error": error,
        },
    )


@router.get("", response_class=HTMLResponse)
def list_categories(request: Request) -> HTMLResponse:
    """Render the categories page."""
    categories = read_categories()
    return templates.TemplateResponse(
        request,
        "categories/list.html",
        {
            "categories": categories,
            "budget_warnings": _budget_warnings(categories),
            "top_uncategorized": top_uncategorized_descriptions(read_ledger()),
            "error": None,
        },
    )


@router.get("/{txn_type}/new", response_class=HTMLResponse)
def new_category_form(request: Request, txn_type: TransactionType) -> HTMLResponse:
    """Render the "add category" form for the dialog."""
    if txn_type is TransactionType.TRANSFER:
        return HTMLResponse("Invalid category type", status_code=404)
    return _render_form(
        request,
        dialog_id="category-dialog",
        dialog_content_id="category-dialog-content",
        kind="category",
        action_url=f"/categories/{txn_type.value}",
        values={"name": "", "icon": "", "budget": ""},
        editing=False,
        show_budget=txn_type is not TransactionType.INCOME,
    )


@router.get("/{txn_type}/{category_name}/edit", response_class=HTMLResponse)
def edit_category_form(
    request: Request, txn_type: TransactionType, category_name: str
) -> HTMLResponse:
    """Render the "edit category" form for the dialog."""
    if txn_type is TransactionType.TRANSFER:
        return HTMLResponse("Invalid category type", status_code=404)
    tree = read_categories().get(txn_type.value, {})
    entry = tree.get(category_name)
    if entry is None:
        return HTMLResponse("Category not found", status_code=404)
    return _render_form(
        request,
        dialog_id="category-dialog",
        dialog_content_id="category-dialog-content",
        kind="category",
        action_url=f"/categories/{txn_type.value}/{category_name}",
        values={
            "name": category_name,
            "icon": entry["icon"],
            "budget": entry["budget"],
        },
        editing=True,
        show_budget=txn_type is not TransactionType.INCOME,
    )


@router.post("/{txn_type}", response_class=HTMLResponse)
def create_category(
    request: Request,
    txn_type: TransactionType,
    name: str = Form(...),
    icon: str = Form(""),
    budget: str = Form(""),
) -> HTMLResponse:
    """Create a new category; close the dialog and refresh the tree on success."""
    values = {"name": name, "icon": icon, "budget": budget}
    try:
        categories = add_category(
            read_categories(), txn_type, name, icon, _parse_budget(budget)
        )
    except ValueError as exc:
        return _render_form(
            request,
            dialog_id="category-dialog",
            dialog_content_id="category-dialog-content",
            kind="category",
            action_url=f"/categories/{txn_type.value}",
            values=values,
            editing=False,
            show_budget=txn_type is not TransactionType.INCOME,
            error=str(exc),
        )
    write_categories(categories)
    return _render_tree(
        request, oob=True, headers=toast("Category added", close_dialog=True)
    )


@router.post("/{txn_type}/{category_name}", response_class=HTMLResponse)
def update_category_route(
    request: Request,
    txn_type: TransactionType,
    category_name: str,
    name: str = Form(...),
    icon: str = Form(""),
    budget: str = Form(""),
) -> HTMLResponse:
    """Rename/re-icon/re-budget an existing category; refresh the tree on success."""
    values = {"name": name, "icon": icon, "budget": budget}
    try:
        categories = update_category(
            read_categories(),
            txn_type,
            category_name,
            name=name,
            icon=icon,
            budget=_parse_budget(budget),
        )
    except ValueError as exc:
        return _render_form(
            request,
            dialog_id="category-dialog",
            dialog_content_id="category-dialog-content",
            kind="category",
            action_url=f"/categories/{txn_type.value}/{category_name}",
            values=values,
            editing=True,
            show_budget=txn_type is not TransactionType.INCOME,
            error=str(exc),
        )
    write_categories(categories)

    rules_renamed = _rename_in_rules(
        category_name,
        name,
        lambda rules: rename_category_in_rules(rules, category_name, name),
    )

    return _render_tree(
        request,
        oob=True,
        headers=_toast_with_rule_count("Category updated", rules_renamed),
    )


@router.post("/{txn_type}/{category_name}/delete", response_class=HTMLResponse)
def delete_category_route(
    request: Request, txn_type: TransactionType, category_name: str
) -> HTMLResponse:
    """Delete a category (and its subcategories) and re-render the tree."""
    try:
        categories = delete_category(read_categories(), txn_type, category_name)
    except ValueError as exc:
        return _render_tree(request, error=str(exc))
    write_categories(categories)
    return _render_tree(request, headers=toast("Category deleted"))


@router.get(
    "/{txn_type}/{category_name}/subcategories/new", response_class=HTMLResponse
)
def new_subcategory_form(
    request: Request, txn_type: TransactionType, category_name: str
) -> HTMLResponse:
    """Render the "add subcategory" form for the dialog."""
    if txn_type is TransactionType.TRANSFER:
        return HTMLResponse("Invalid category type", status_code=404)
    if category_name not in read_categories().get(txn_type.value, {}):
        return HTMLResponse("Category not found", status_code=404)
    return _render_form(
        request,
        dialog_id="subcategory-dialog",
        dialog_content_id="subcategory-dialog-content",
        kind="subcategory",
        action_url=f"/categories/{txn_type.value}/{category_name}/subcategories",
        values={"name": "", "icon": "", "budget": ""},
        editing=False,
        show_budget=txn_type is not TransactionType.INCOME,
    )


@router.post("/{txn_type}/{category_name}/subcategories", response_class=HTMLResponse)
def create_subcategory(
    request: Request,
    txn_type: TransactionType,
    category_name: str,
    name: str = Form(...),
    icon: str = Form(""),
    budget: str = Form(""),
) -> HTMLResponse:
    """Create a new subcategory; close the dialog and refresh the tree on success."""
    values = {"name": name, "icon": icon, "budget": budget}
    try:
        categories = add_subcategory(
            read_categories(),
            txn_type,
            category_name,
            name,
            icon,
            _parse_budget(budget),
        )
    except ValueError as exc:
        return _render_form(
            request,
            dialog_id="subcategory-dialog",
            dialog_content_id="subcategory-dialog-content",
            kind="subcategory",
            action_url=f"/categories/{txn_type.value}/{category_name}/subcategories",
            values=values,
            editing=False,
            show_budget=txn_type is not TransactionType.INCOME,
            error=str(exc),
        )
    write_categories(categories)
    return _render_tree(
        request, oob=True, headers=toast("Subcategory added", close_dialog=True)
    )


@router.get(
    "/{txn_type}/{category_name}/subcategories/{sub_name}/edit",
    response_class=HTMLResponse,
)
def edit_subcategory_form(
    request: Request, txn_type: TransactionType, category_name: str, sub_name: str
) -> HTMLResponse:
    """Render the "edit subcategory" form for the dialog."""
    if txn_type is TransactionType.TRANSFER:
        return HTMLResponse("Invalid category type", status_code=404)
    tree = read_categories().get(txn_type.value, {})
    entry = tree.get(category_name)
    if entry is None or sub_name not in entry["subcategories"]:
        return HTMLResponse("Subcategory not found", status_code=404)
    sub_entry = entry["subcategories"][sub_name]
    return _render_form(
        request,
        dialog_id="subcategory-dialog",
        dialog_content_id="subcategory-dialog-content",
        kind="subcategory",
        action_url=f"/categories/{txn_type.value}/{category_name}/subcategories/{sub_name}",
        values={
            "name": sub_name,
            "icon": sub_entry["icon"],
            "budget": sub_entry["budget"],
        },
        editing=True,
        show_budget=txn_type is not TransactionType.INCOME,
    )


@router.post(
    "/{txn_type}/{category_name}/subcategories/{sub_name}",
    response_class=HTMLResponse,
)
def update_subcategory_route(
    request: Request,
    txn_type: TransactionType,
    category_name: str,
    sub_name: str,
    name: str = Form(...),
    icon: str = Form(""),
    budget: str = Form(""),
) -> HTMLResponse:
    """Rename/re-icon/re-budget an existing subcategory; refresh the tree on success."""
    values = {"name": name, "icon": icon, "budget": budget}
    try:
        categories = update_subcategory(
            read_categories(),
            txn_type,
            category_name,
            sub_name,
            name=name,
            icon=icon,
            budget=_parse_budget(budget),
        )
    except ValueError as exc:
        return _render_form(
            request,
            dialog_id="subcategory-dialog",
            dialog_content_id="subcategory-dialog-content",
            kind="subcategory",
            action_url=(
                f"/categories/{txn_type.value}/{category_name}/subcategories/{sub_name}"
            ),
            values=values,
            editing=True,
            show_budget=txn_type is not TransactionType.INCOME,
            error=str(exc),
        )
    write_categories(categories)

    rules_renamed = _rename_in_rules(
        sub_name,
        name,
        lambda rules: rename_subcategory_in_rules(rules, category_name, sub_name, name),
    )

    return _render_tree(
        request,
        oob=True,
        headers=_toast_with_rule_count("Subcategory updated", rules_renamed),
    )


@router.post(
    "/{txn_type}/{category_name}/subcategories/{sub_name}/delete",
    response_class=HTMLResponse,
)
def delete_subcategory_route(
    request: Request, txn_type: TransactionType, category_name: str, sub_name: str
) -> HTMLResponse:
    """Delete a subcategory and re-render the tree."""
    try:
        categories = delete_subcategory(
            read_categories(), txn_type, category_name, sub_name
        )
    except ValueError as exc:
        return _render_tree(request, error=str(exc))
    write_categories(categories)
    return _render_tree(request, headers=toast("Subcategory deleted"))
