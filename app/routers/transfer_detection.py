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
    OrphanTransferCandidate,
    TransferMatch,
    apply_transfer_matches,
    find_orphan_transfer_candidates,
    find_transfer_matches,
    synthesize_and_merge_transfer,
)
from app.storage.accounts import read_accounts
from app.storage.ledger import read_ledger, write_ledger
from app.templating import templates

router = APIRouter(prefix="/import", tags=["import"])


@router.get("/detect-transfers/preview", response_class=HTMLResponse)
def preview_transfer_matches(request: Request) -> HTMLResponse:
    """Compute and render candidate transfer pairs against the whole ledger.

    Read-only, per CLAUDE.md's "bulk [changes] always preview first"
    invariant, extended here to transfer detection. Two kinds of
    candidate, both previewed before anything is written: ``matches``
    (both legs already exist — see ``find_transfer_matches``) and
    ``orphans`` (only one leg exists; the other account is registered
    but has no matching row at all — see
    ``find_orphan_transfer_candidates``). Each gets its own hidden JSON
    payload, same no-server-session pattern as the reclassify preview
    and the import wizard's own ``rows_payload``.
    """
    accounts_list = read_accounts()
    accounts = {account.id: account for account in accounts_list}
    ledger = read_ledger()
    matches = find_transfer_matches(ledger, accounts_list)
    orphans = find_orphan_transfer_candidates(ledger, accounts_list)
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
    orphans_payload = json.dumps(
        [
            {
                "transaction_id": candidate.transaction_id,
                "date": candidate.date.isoformat(),
                "account_id": candidate.account_id,
                "counterparty_account_id": candidate.counterparty_account_id,
                "amount": str(candidate.amount),
                "description": candidate.description,
            }
            for candidate in orphans
        ]
    )
    return templates.TemplateResponse(
        request,
        "import/_detect_transfers_preview.html",
        {
            "matches": matches,
            "orphans": orphans,
            "accounts": accounts,
            "matches_payload": matches_payload,
            "orphans_payload": orphans_payload,
        },
    )


@router.post("/detect-transfers/apply", response_class=HTMLResponse)
def apply_transfer_matches_route(
    request: Request,
    matches_payload: str = Form(...),
    orphans_payload: str = Form("[]"),
    selected: list[str] = Form([]),
    selected_orphans: list[str] = Form([]),
) -> HTMLResponse:
    """Apply the checked subset of previewed matches and orphan candidates.

    Rebuilds ``TransferMatch``/``OrphanTransferCandidate`` objects from
    the previewed payloads (never recomputed) so what gets written can't
    disagree with what the user saw — same discipline as
    ``rules.apply_reclassification_route``. Matches merge two already-
    real rows; orphan candidates additionally *create* the missing leg
    (a genuinely inferred transaction, never independently observed in
    any bank export — see ``services.transactions
    .find_orphan_transfer_candidates``), which is why they're selected
    separately from ordinary matches and default unchecked in the
    template rather than defaulting to "apply everything found."
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

    raw_orphans = json.loads(orphans_payload)
    selected_orphan_ids = set(selected_orphans)
    orphan_candidates = [
        OrphanTransferCandidate(
            transaction_id=raw["transaction_id"],
            date=date_.fromisoformat(raw["date"]),
            account_id=raw["account_id"],
            counterparty_account_id=raw["counterparty_account_id"],
            amount=Decimal(raw["amount"]),
            description=raw["description"],
        )
        for raw in raw_orphans
        if raw["transaction_id"] in selected_orphan_ids
    ]

    ledger = apply_transfer_matches(read_ledger(), matches)
    for candidate in orphan_candidates:
        try:
            ledger = synthesize_and_merge_transfer(ledger, candidate)
        except ValueError:
            continue
    write_ledger(ledger)

    count = len(matches) + len(orphan_candidates)
    noun = "pair" if count == 1 else "pairs"
    return HTMLResponse(
        "",
        headers=toast(
            f"Merged {count} transaction {noun} into transfers", close_dialog=True
        ),
    )
