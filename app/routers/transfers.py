"""Routes for recording transfers between accounts, via the transfer dialog."""

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.routers.htmx_events import toast
from app.routers.transactions import render_table as render_transactions_table
from app.services.transactions import new_transfer_pair, update_transfer_pair
from app.storage.accounts import read_accounts
from app.storage.ledger import read_ledger, write_ledger
from app.templating import templates

router = APIRouter(prefix="/transfers", tags=["transfers"])


def _render_form(
    request: Request,
    *,
    values: dict[str, str],
    error: str | None = None,
    transfer_id: str | None = None,
) -> HTMLResponse:
    """Render the transfer form fragment shown inside the dialog.

    ``transfer_id`` set means this is an edit (posts back to
    ``/transfers/{id}`` instead of ``/transfers``); ``None`` means a new
    transfer.
    """
    return templates.TemplateResponse(
        request,
        "transfers/_form.html",
        {
            "accounts": read_accounts(),
            "values": values,
            "error": error,
            "transfer_id": transfer_id,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_transfer_form(request: Request) -> HTMLResponse:
    """Render the "record transfer" form for the dialog."""
    values = {
        "from_account_id": "",
        "to_account_id": "",
        "date": date.today().isoformat(),
        "amount": "",
        "description": "",
        "notes": "",
    }
    return _render_form(request, values=values)


@router.get("/{transfer_id}/edit", response_class=HTMLResponse)
def edit_transfer_form(request: Request, transfer_id: str) -> HTMLResponse:
    """Render the edit form for an existing transfer, prefilled from both legs."""
    legs = [t for t in read_ledger() if t.transfer_id == transfer_id]
    outflow = next((t for t in legs if t.amount < 0), None)
    inflow = next((t for t in legs if t.amount > 0), None)
    if outflow is None or inflow is None:
        return HTMLResponse("Transfer not found", status_code=404)
    values = {
        "from_account_id": outflow.account_id,
        "to_account_id": inflow.account_id,
        "date": outflow.date.isoformat(),
        "amount": str(abs(outflow.amount)),
        "description": outflow.description,
        "notes": outflow.notes or "",
    }
    return _render_form(request, values=values, transfer_id=transfer_id)


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
    """Record a transfer as a linked two-row pair.

    On success, closes the dialog and refreshes the transactions table
    (out-of-band, since this router doesn't own that fragment) with the two
    new rows. On error, re-renders the form in place with the error and the
    user's input preserved.
    """
    values = {
        "from_account_id": from_account_id,
        "to_account_id": to_account_id,
        "date": date.isoformat(),
        "amount": amount,
        "description": description,
        "notes": notes,
    }
    account_ids = [account.id for account in read_accounts()]
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
        return _render_form(request, values=values, error=str(exc))

    transactions = read_ledger()
    transactions.extend([outflow, inflow])
    write_ledger(transactions)

    return render_transactions_table(
        request, oob=True, headers=toast("Transfer recorded", close_dialog=True)
    )


@router.post("/{transfer_id}", response_class=HTMLResponse)
def update_transfer_route(
    request: Request,
    transfer_id: str,
    from_account_id: str = Form(...),
    to_account_id: str = Form(...),
    date: date = Form(...),
    amount: str = Form(...),
    description: str = Form(""),
    notes: str = Form(""),
) -> HTMLResponse:
    """Update both legs of an existing transfer together.

    On success, closes the dialog and refreshes the transactions table
    (out-of-band). On error, re-renders the form in place with the error
    and the user's input preserved.
    """
    values = {
        "from_account_id": from_account_id,
        "to_account_id": to_account_id,
        "date": date.isoformat(),
        "amount": amount,
        "description": description,
        "notes": notes,
    }
    account_ids = [account.id for account in read_accounts()]
    ledger = read_ledger()
    try:
        parsed_amount = Decimal(amount)
        ledger = update_transfer_pair(
            ledger,
            transfer_id,
            account_ids,
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            date=date,
            amount=parsed_amount,
            description=description,
            notes=notes or None,
        )
    except (ValueError, InvalidOperation) as exc:
        return _render_form(
            request, values=values, error=str(exc), transfer_id=transfer_id
        )

    write_ledger(ledger)

    return render_transactions_table(
        request, oob=True, headers=toast("Transfer updated", close_dialog=True)
    )
