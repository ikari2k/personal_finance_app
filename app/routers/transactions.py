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


def _render_table(request: Request, error: str | None = None) -> HTMLResponse:
    """Render the transaction list table fragment, newest first."""
    transactions = sorted(read_ledger(), key=lambda t: t.date, reverse=True)
    accounts = {account.id: account for account in read_accounts()}
    return templates.TemplateResponse(
        request,
        "transactions/_table.html",
        {"transactions": transactions, "accounts": accounts, "error": error},
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


@router.get("/new", response_class=HTMLResponse)
def new_transaction_form(request: Request) -> HTMLResponse:
    """Render the "add transaction" form."""
    categories = read_categories()
    all_subcategories = sorted({sub for subs in categories.values() for sub in subs})
    return templates.TemplateResponse(
        request,
        "transactions/_form.html",
        {
            "accounts": read_accounts(),
            "categories": sorted(categories),
            "subcategories": all_subcategories,
            "today": date.today(),
            "error": None,
        },
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
    """Record a new transaction and re-render the transaction list."""
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
        return _render_table(request, error=str(exc))

    write_categories(ensure_category(read_categories(), category, subcategory))

    transactions = read_ledger()
    transactions.append(transaction)
    write_ledger(transactions)

    return _render_table(request)
