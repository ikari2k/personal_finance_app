"""Routes for managing the income/expense category and subcategory trees."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.models.category import ICON_HINTS, VALID_ICONS
from app.models.transaction import TransactionType
from app.routers.htmx_events import toast
from app.services.categories import (
    add_category,
    add_subcategory,
    delete_category,
    delete_subcategory,
    update_category,
    update_subcategory,
)
from app.storage.categories import read_categories, write_categories
from app.templating import templates

router = APIRouter(prefix="/categories", tags=["categories"])

ICON_KEYS = sorted(VALID_ICONS)


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
    return templates.TemplateResponse(
        request,
        "categories/_tree.html",
        {"categories": read_categories(), "oob": oob, "error": error},
        headers=headers,
    )


def _render_form(
    request: Request,
    *,
    dialog_id: str,
    dialog_content_id: str,
    kind: str,
    action_url: str,
    values: dict[str, str],
    editing: bool,
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
            "icon_keys": ICON_KEYS,
            "icon_hints": ICON_HINTS,
            "error": error,
        },
    )


@router.get("", response_class=HTMLResponse)
def list_categories(request: Request) -> HTMLResponse:
    """Render the categories page."""
    return templates.TemplateResponse(
        request,
        "categories/list.html",
        {"categories": read_categories(), "error": None},
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
        values={"name": "", "icon": ""},
        editing=False,
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
        values={"name": category_name, "icon": entry["icon"]},
        editing=True,
    )


@router.post("/{txn_type}", response_class=HTMLResponse)
def create_category(
    request: Request,
    txn_type: TransactionType,
    name: str = Form(...),
    icon: str = Form(""),
) -> HTMLResponse:
    """Create a new category; close the dialog and refresh the tree on success."""
    values = {"name": name, "icon": icon}
    try:
        categories = add_category(read_categories(), txn_type, name, icon)
    except ValueError as exc:
        return _render_form(
            request,
            dialog_id="category-dialog",
            dialog_content_id="category-dialog-content",
            kind="category",
            action_url=f"/categories/{txn_type.value}",
            values=values,
            editing=False,
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
) -> HTMLResponse:
    """Rename/re-icon an existing category; close the dialog and refresh the tree."""
    values = {"name": name, "icon": icon}
    try:
        categories = update_category(
            read_categories(), txn_type, category_name, name=name, icon=icon
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
            error=str(exc),
        )
    write_categories(categories)
    return _render_tree(
        request, oob=True, headers=toast("Category updated", close_dialog=True)
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
        values={"name": "", "icon": ""},
        editing=False,
    )


@router.post("/{txn_type}/{category_name}/subcategories", response_class=HTMLResponse)
def create_subcategory(
    request: Request,
    txn_type: TransactionType,
    category_name: str,
    name: str = Form(...),
    icon: str = Form(""),
) -> HTMLResponse:
    """Create a new subcategory; close the dialog and refresh the tree on success."""
    values = {"name": name, "icon": icon}
    try:
        categories = add_subcategory(
            read_categories(), txn_type, category_name, name, icon
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
    return _render_form(
        request,
        dialog_id="subcategory-dialog",
        dialog_content_id="subcategory-dialog-content",
        kind="subcategory",
        action_url=f"/categories/{txn_type.value}/{category_name}/subcategories/{sub_name}",
        values={"name": sub_name, "icon": entry["subcategories"][sub_name]},
        editing=True,
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
) -> HTMLResponse:
    """Rename/re-icon an existing subcategory; close the dialog and refresh the tree."""
    values = {"name": name, "icon": icon}
    try:
        categories = update_subcategory(
            read_categories(), txn_type, category_name, sub_name, name=name, icon=icon
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
            error=str(exc),
        )
    write_categories(categories)
    return _render_tree(
        request, oob=True, headers=toast("Subcategory updated", close_dialog=True)
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
