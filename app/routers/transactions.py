"""Routes for manual transaction entry and browsing the ledger."""

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.models.transaction import TransactionType
from app.routers.htmx_events import toast
from app.services.aggregation import grouped_transaction_view
from app.services.transactions import (
    ensure_category,
    new_transaction,
    remove_transaction,
    update_transaction,
)
from app.storage.accounts import read_accounts
from app.storage.categories import read_categories, write_categories
from app.storage.ledger import read_ledger, write_ledger
from app.templating import templates

router = APIRouter(prefix="/transactions", tags=["transactions"])


def render_table(
    request: Request,
    *,
    oob: bool = False,
    error: str | None = None,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    """Render the transaction list fragment, grouped by month and by type.

    Always uses the default view (both groupings on, no account filter):
    this is called after creating a transaction/transfer, which has no way
    to know what grouping/filter the page currently has selected, so it
    deliberately resets to the default rather than guessing. ``oob=True``
    marks the fragment as an out-of-band swap target — used by
    ``app.routers.transfers`` to refresh this table from the transfer
    dialog, whose own response targets the dialog's content area instead.
    Public (no leading underscore) because it's reused across routers.
    """
    accounts_list = read_accounts()
    months = grouped_transaction_view(read_ledger(), by_month=True, by_type=True)
    return templates.TemplateResponse(
        request,
        "transactions/_table.html",
        {
            "months": months,
            "accounts": {account.id: account for account in accounts_list},
            "accounts_list": accounts_list,
            "by_month": True,
            "by_type": True,
            "account_id": "",
            "oob": oob,
            "error": error,
        },
        headers=headers,
    )


@router.get("", response_class=HTMLResponse)
def list_transactions(
    request: Request, by_month: bool = True, by_type: bool = True, account_id: str = ""
) -> HTMLResponse:
    """Render the transaction list.

    Full navigation renders the whole page; an HTMX request (from the
    grouping-toggle buttons or the account filter) renders just the table
    fragment they swap in. ``account_id`` filters to one account's rows
    before grouping when set; an empty string (the default) means "all
    accounts".
    """
    ledger = read_ledger()
    if account_id:
        ledger = [t for t in ledger if t.account_id == account_id]
    accounts_list = read_accounts()
    months = grouped_transaction_view(ledger, by_month=by_month, by_type=by_type)
    context = {
        "months": months,
        "accounts": {account.id: account for account in accounts_list},
        "accounts_list": accounts_list,
        "by_month": by_month,
        "by_type": by_type,
        "account_id": account_id,
        "error": None,
    }
    template = (
        "transactions/_table.html"
        if request.headers.get("HX-Request") == "true"
        else "transactions/list.html"
    )
    return templates.TemplateResponse(request, template, context)


def _render_form(
    request: Request,
    *,
    txn_type: TransactionType,
    values: dict[str, str],
    error: str | None = None,
    transaction_id: str | None = None,
) -> HTMLResponse:
    """Render the income/expense entry/edit form fragment shown inside its dialog.

    Categories/subcategories are filtered to ``txn_type``'s own tree —
    income and expense never share categories (see ``app.models.category``).
    ``transaction_id`` set means this is an edit (posts back to
    ``/transactions/{id}`` instead of ``/transactions``); ``None`` means a
    new transaction.
    """
    tree = read_categories().get(txn_type.value, {})
    return templates.TemplateResponse(
        request,
        "transactions/_form.html",
        {
            "accounts": read_accounts(),
            "categories": sorted(tree),
            "subcategories": sorted({sub for subs in tree.values() for sub in subs}),
            "values": values,
            "error": error,
            "dialog_id": f"{txn_type.value}-dialog",
            "dialog_content_id": f"{txn_type.value}-dialog-content",
            "transaction_id": transaction_id,
        },
    )


@router.get("/new/{txn_type}", response_class=HTMLResponse)
def new_transaction_form(request: Request, txn_type: TransactionType) -> HTMLResponse:
    """Render the prefilled "add income"/"add expense" form for its dialog."""
    if txn_type is TransactionType.TRANSFER:
        return HTMLResponse("Invalid transaction type", status_code=404)
    values = {
        "account_id": "",
        "date": date.today().isoformat(),
        "type": txn_type.value,
        "category": "",
        "subcategory": "",
        "description": "",
        "amount": "",
        "notes": "",
    }
    return _render_form(request, txn_type=txn_type, values=values)


@router.get("/{transaction_id}/edit", response_class=HTMLResponse)
def edit_transaction_form(request: Request, transaction_id: str) -> HTMLResponse:
    """Render the edit form for an existing income/expense transaction.

    Transfers can't be edited this way (only deleted, as a pair) — see
    ``services.transactions.update_transaction`` for why.
    """
    transaction = next((t for t in read_ledger() if t.id == transaction_id), None)
    if transaction is None:
        return HTMLResponse("Transaction not found", status_code=404)
    if transaction.type is TransactionType.TRANSFER:
        return HTMLResponse(
            "Transfers can't be edited directly; delete and re-record instead.",
            status_code=400,
        )
    values = {
        "account_id": transaction.account_id,
        "date": transaction.date.isoformat(),
        "type": transaction.type.value,
        "category": transaction.category,
        "subcategory": transaction.subcategory,
        "description": transaction.description,
        "amount": str(abs(transaction.amount)),
        "notes": transaction.notes or "",
    }
    return _render_form(
        request, txn_type=transaction.type, values=values, transaction_id=transaction_id
    )


@router.post("", response_class=HTMLResponse)
def create_transaction(
    request: Request,
    account_id: str = Form(...),
    date: date = Form(...),
    type: TransactionType = Form(...),
    category: str = Form(...),
    subcategory: str = Form(""),
    description: str = Form(""),
    amount: str = Form(...),
    notes: str = Form(""),
) -> HTMLResponse:
    """Record a new transaction.

    On success, closes the dialog and refreshes the transaction list
    (out-of-band). On error, re-renders the form in place with the error
    and the user's input preserved.
    """
    values = {
        "account_id": account_id,
        "date": date.isoformat(),
        "type": type.value,
        "category": category,
        "subcategory": subcategory,
        "description": description,
        "amount": amount,
        "notes": notes,
    }
    account_ids = [account.id for account in read_accounts()]
    try:
        parsed_amount = Decimal(amount)
        transaction = new_transaction(
            account_ids,
            account_id=account_id,
            date=date,
            category=category,
            subcategory=subcategory,
            description=description,
            amount=parsed_amount,
            type=type,
            notes=notes or None,
        )
    except (ValueError, InvalidOperation) as exc:
        return _render_form(request, txn_type=type, values=values, error=str(exc))

    write_categories(ensure_category(read_categories(), type, category, subcategory))

    transactions = read_ledger()
    transactions.append(transaction)
    write_ledger(transactions)

    return render_table(
        request,
        oob=True,
        headers=toast(f"{type.value.capitalize()} added", close_dialog=True),
    )


@router.post("/{transaction_id}", response_class=HTMLResponse)
def update_transaction_route(
    request: Request,
    transaction_id: str,
    account_id: str = Form(...),
    date: date = Form(...),
    type: TransactionType = Form(...),
    category: str = Form(...),
    subcategory: str = Form(""),
    description: str = Form(""),
    amount: str = Form(...),
    notes: str = Form(""),
) -> HTMLResponse:
    """Update an existing income/expense transaction.

    On success, closes the dialog and refreshes the transaction list
    (out-of-band). On error (including "this is a transfer"), re-renders
    the form in place with the error and the user's input preserved.
    """
    values = {
        "account_id": account_id,
        "date": date.isoformat(),
        "type": type.value,
        "category": category,
        "subcategory": subcategory,
        "description": description,
        "amount": amount,
        "notes": notes,
    }
    account_ids = [account.id for account in read_accounts()]
    ledger = read_ledger()
    try:
        parsed_amount = Decimal(amount)
        ledger = update_transaction(
            ledger,
            transaction_id,
            account_ids,
            account_id=account_id,
            date=date,
            category=category,
            subcategory=subcategory,
            description=description,
            amount=parsed_amount,
            type=type,
            notes=notes or None,
        )
    except (ValueError, InvalidOperation) as exc:
        return _render_form(
            request,
            txn_type=type,
            values=values,
            error=str(exc),
            transaction_id=transaction_id,
        )

    write_categories(ensure_category(read_categories(), type, category, subcategory))
    write_ledger(ledger)

    return render_table(
        request,
        oob=True,
        headers=toast(f"{type.value.capitalize()} updated", close_dialog=True),
    )


@router.post("/{transaction_id}/delete", response_class=HTMLResponse)
def delete_transaction(request: Request, transaction_id: str) -> HTMLResponse:
    """Delete a transaction (both legs, if it's a transfer) and re-render the list."""
    ledger = read_ledger()
    target = next((t for t in ledger if t.id == transaction_id), None)
    try:
        ledger = remove_transaction(ledger, transaction_id)
    except ValueError as exc:
        return render_table(request, error=str(exc))
    write_ledger(ledger)
    message = (
        "Transfer deleted"
        if target and target.type is TransactionType.TRANSFER
        else "Transaction deleted"
    )
    return render_table(request, headers=toast(message))
