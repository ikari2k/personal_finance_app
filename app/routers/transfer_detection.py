"""Routes for detecting transfers among already-imported transactions.

Split out of ``app.routers.import_`` (which stayed the CSV-import wizard/
edit/run flows only) since this feature isn't about parsing a CSV — it's
about ``services.transactions.find_transfer_matches`` cross-referencing
the ledger against itself. Mounted at the same ``/import`` prefix as
``import_.router`` (not its own URL namespace) purely for continuity: the
"Detect transfers" dialog is triggered from the `/import` dashboard page,
and no template or test needed to change when this moved here.
"""

import json
from datetime import date as date_
from decimal import Decimal

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.routers.htmx_events import toast
from app.services.transactions import (
    TransferMatch,
    apply_transfer_matches,
    find_transfer_matches,
)
from app.storage.accounts import read_accounts
from app.storage.ledger import read_ledger, write_ledger
from app.templating import templates

router = APIRouter(prefix="/import", tags=["import"])


@router.get("/detect-transfers/preview", response_class=HTMLResponse)
def preview_transfer_matches(request: Request) -> HTMLResponse:
    """Compute and render candidate transfer pairs against the whole ledger.

    Read-only, per CLAUDE.md's "bulk [changes] always preview first"
    invariant, extended here to transfer detection. The match list is
    threaded to the apply step as a hidden JSON field, same
    no-server-session pattern as the reclassify preview and the import
    wizard's own ``rows_payload``.
    """
    accounts_list = read_accounts()
    accounts = {account.id: account for account in accounts_list}
    matches = find_transfer_matches(read_ledger(), accounts_list)
    matches_payload = json.dumps(
        [
            {
                "from_transaction_id": match.from_transaction_id,
                "to_transaction_id": match.to_transaction_id,
                "date": match.date.isoformat(),
                "from_account_id": match.from_account_id,
                "to_account_id": match.to_account_id,
                "amount": str(match.amount),
                "from_description": match.from_description,
                "to_description": match.to_description,
            }
            for match in matches
        ]
    )
    return templates.TemplateResponse(
        request,
        "import/_detect_transfers_preview.html",
        {"matches": matches, "accounts": accounts, "matches_payload": matches_payload},
    )


@router.post("/detect-transfers/apply", response_class=HTMLResponse)
def apply_transfer_matches_route(
    request: Request,
    matches_payload: str = Form(...),
    selected: list[str] = Form([]),
) -> HTMLResponse:
    """Merge the checked subset of previewed matches into linked transfers.

    Rebuilds ``TransferMatch`` objects from the previewed payload (never
    recomputed) so what gets written can't disagree with what the user
    saw — same discipline as ``rules.apply_reclassification_route``. Only
    matches whose checkbox (keyed by ``from_transaction_id``, the one
    stable id per row in the preview table) is present in ``selected``
    are applied; unlike reclassification, a wrongly-merged transfer has
    no dedicated "split back apart" UI yet, so letting the user exclude
    an individual pair before it's ever written is worth the extra
    control.
    """
    raw_matches = json.loads(matches_payload)
    selected_ids = set(selected)
    matches = [
        TransferMatch(
            from_transaction_id=raw["from_transaction_id"],
            to_transaction_id=raw["to_transaction_id"],
            date=date_.fromisoformat(raw["date"]),
            from_account_id=raw["from_account_id"],
            to_account_id=raw["to_account_id"],
            amount=Decimal(raw["amount"]),
            from_description=raw["from_description"],
            to_description=raw["to_description"],
        )
        for raw in raw_matches
        if raw["from_transaction_id"] in selected_ids
    ]
    write_ledger(apply_transfer_matches(read_ledger(), matches))

    count = len(matches)
    noun = "pair" if count == 1 else "pairs"
    return HTMLResponse(
        "",
        headers=toast(
            f"Merged {count} transaction {noun} into transfers", close_dialog=True
        ),
    )
