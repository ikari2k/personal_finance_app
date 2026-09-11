"""Routes for viewing and managing accounts."""

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.models.account import Account
from app.services.accounts import add_account, remove_account, update_account
from app.services.balances import all_balances
from app.storage.accounts import read_accounts, write_accounts
from app.storage.ledger import read_ledger
from app.templating import templates

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _render_table(request: Request, error: str | None = None) -> HTMLResponse:
    """Render the accounts table fragment, used by every mutating route."""
    accounts = read_accounts()
    balances = all_balances(accounts, read_ledger())
    return templates.TemplateResponse(
        request,
        "accounts/_table.html",
        {"accounts": accounts, "balances": balances, "error": error},
    )


@router.get("", response_class=HTMLResponse)
def list_accounts(request: Request) -> HTMLResponse:
    """Render the accounts page."""
    accounts = read_accounts()
    balances = all_balances(accounts, read_ledger())
    return templates.TemplateResponse(
        request,
        "accounts/list.html",
        {"accounts": accounts, "balances": balances, "error": None},
    )


@router.get("/new", response_class=HTMLResponse)
def new_account_form(request: Request) -> HTMLResponse:
    """Render the inline "add account" form."""
    return templates.TemplateResponse(
        request, "accounts/_form.html", {"account": None, "error": None}
    )


@router.get("/{account_id}/edit", response_class=HTMLResponse)
def edit_account_form(request: Request, account_id: str) -> HTMLResponse:
    """Render the inline "edit account" form for ``account_id``."""
    account = next((a for a in read_accounts() if a.id == account_id), None)
    if account is None:
        return HTMLResponse("Account not found", status_code=404)
    return templates.TemplateResponse(
        request, "accounts/_form.html", {"account": account, "error": None}
    )


@router.post("", response_class=HTMLResponse)
def create_account(
    request: Request,
    account_id: str = Form(...),
    name: str = Form(...),
    number: str = Form(""),
    description: str = Form(""),
    starting_balance: str = Form("0"),
) -> HTMLResponse:
    """Create a new account and re-render the accounts table."""
    accounts = read_accounts()
    try:
        balance = Decimal(starting_balance)
        new_account = Account(
            id=account_id,
            name=name,
            number=number,
            description=description,
            starting_balance=balance,
        )
        accounts = add_account(accounts, new_account)
    except (ValueError, InvalidOperation) as exc:
        return _render_table(request, error=str(exc))
    write_accounts(accounts)
    return _render_table(request)


@router.post("/{account_id}", response_class=HTMLResponse)
def edit_account(
    request: Request,
    account_id: str,
    name: str = Form(...),
    number: str = Form(""),
    description: str = Form(""),
    starting_balance: str = Form("0"),
) -> HTMLResponse:
    """Update an existing account and re-render the accounts table."""
    accounts = read_accounts()
    try:
        balance = Decimal(starting_balance)
        accounts = update_account(
            accounts,
            account_id,
            name=name,
            number=number,
            description=description,
            starting_balance=balance,
        )
    except (ValueError, InvalidOperation) as exc:
        return _render_table(request, error=str(exc))
    write_accounts(accounts)
    return _render_table(request)


@router.post("/{account_id}/delete", response_class=HTMLResponse)
def delete_account(request: Request, account_id: str) -> HTMLResponse:
    """Delete an account (if it has no transactions) and re-render the table."""
    accounts = read_accounts()
    transactions = read_ledger()
    try:
        accounts = remove_account(accounts, transactions, account_id)
    except ValueError as exc:
        return _render_table(request, error=str(exc))
    write_accounts(accounts)
    return _render_table(request)
