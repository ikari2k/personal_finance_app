"""Routes for manual transaction entry and browsing the ledger."""

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.models.transaction import TransactionType
from app.services.transactions import ensure_category, new_transaction
from app.storage.accounts import read_accounts
from app.storage.categories import read_categories, write_categories
from app.storage.ledger import read_ledger, write_ledger
from app.templating import templates

router = APIRouter(prefix="/transactions", tags=["transactions"])

CLOSE_DIALOG = {"HX-Trigger": "close-dialog"}


def render_table(
    request: Request,
    *,
    oob: bool = False,
    error: str | None = None,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    """Render the transaction list table fragment, newest first.

    ``oob=True`` marks the fragment as an out-of-band swap target — used by
    ``app.routers.transfers`` to refresh this table from the transfer
    dialog, whose own response targets the dialog's content area instead.
    Public (no leading underscore) because it's reused across routers.
    """
    transactions = sorted(read_ledger(), key=lambda t: t.date, reverse=True)
    accounts = {account.id: account for account in read_accounts()}
    return templates.TemplateResponse(
        request,
        "transactions/_table.html",
        {
            "transactions": transactions,
            "accounts": accounts,
            "oob": oob,
            "error": error,
        },
        headers=headers,
    )


@router.get("", response_class=HTMLResponse)
def list_transactions(request: Request) -> HTMLResponse:
    """Render the transaction list page."""
    transactions = sorted(read_ledger(), key=lambda t: t.date, reverse=True)
    accounts = {account.id: account for account in read_accounts()}
    return templates.TemplateResponse(
        request,
        "transactions/list.html",
        {"transactions": transactions, "accounts": accounts, "error": None},
    )


def _render_form(
    request: Request,
    *,
    txn_type: TransactionType,
    values: dict[str, str],
    error: str | None = None,
) -> HTMLResponse:
    """Render the income/expense entry form fragment shown inside its dialog.

    Categories/subcategories are filtered to ``txn_type``'s own tree —
    income and expense never share categories (see ``app.models.category``).
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

    return render_table(request, oob=True, headers=CLOSE_DIALOG)
