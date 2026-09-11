"""Routes for recording transfers between accounts."""

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.services.transactions import new_transfer_pair
from app.storage.accounts import read_accounts
from app.storage.ledger import read_ledger, write_ledger
from app.templating import templates

router = APIRouter(prefix="/transfers", tags=["transfers"])


@router.get("/new", response_class=HTMLResponse)
def new_transfer_form(request: Request) -> HTMLResponse:
    """Render the "record transfer" form."""
    return templates.TemplateResponse(
        request,
        "transfers/new.html",
        {"accounts": read_accounts(), "today": date.today(), "error": None},
    )


@router.post("", response_class=HTMLResponse)
def create_transfer(
    request: Request,
    from_account_id: str = Form(...),
    to_account_id: str = Form(...),
    date: date = Form(...),
    amount: str = Form(...),
    description: str = Form(""),
    notes: str = Form(""),
) -> HTMLResponse:
    """Record a transfer as a linked two-row pair and show a confirmation."""
    accounts = read_accounts()
    account_ids = [account.id for account in accounts]
    try:
        parsed_amount = Decimal(amount)
        outflow, inflow = new_transfer_pair(
            account_ids,
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            date=date,
            amount=parsed_amount,
            description=description,
            notes=notes or None,
        )
    except (ValueError, InvalidOperation) as exc:
        return templates.TemplateResponse(
            request,
            "transfers/_form.html",
            {"accounts": accounts, "today": date, "error": str(exc)},
        )

    transactions = read_ledger()
    transactions.extend([outflow, inflow])
    write_ledger(transactions)

    accounts_by_id = {account.id: account for account in accounts}
    return templates.TemplateResponse(
        request,
        "transfers/_confirmation.html",
        {"outflow": outflow, "inflow": inflow, "accounts": accounts_by_id},
    )
