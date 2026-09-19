"""Integration tests for the "Detect transfers" preview/apply flow.

Split out of tests/integration/test_import_router.py alongside the
app.routers.transfer_detection module split — these routes are mounted
under /import for UI continuity, but no longer live in app.routers.import_.
"""

import html
from datetime import date
from decimal import Decimal

from app import config
from app.models.account import Account
from app.models.transaction import Transaction, TransactionType
from app.storage.accounts import write_accounts
from app.storage.ledger import read_ledger, write_ledger


def _extract_hidden_value(page_html: str, name: str) -> str:
    """Extract a hidden input's value, HTML-unescaped as a browser would."""
    marker = f'name="{name}" value="'
    start = page_html.index(marker) + len(marker)
    end = page_html.index('"', start)
    return html.unescape(page_html[start:end])


def _seed_transfer_candidates(
    client,
) -> tuple[Transaction, Transaction]:
    """Two accounts plus a plain income/expense row pair that looks like a transfer."""
    write_accounts(
        [
            Account(
                id="chk", name="Checking", number="1111", starting_balance=Decimal("0")
            ),
            Account(
                id="sav", name="Savings", number="2222", starting_balance=Decimal("0")
            ),
        ],
        config.ACCOUNTS_PATH,
    )
    outflow = Transaction(
        id="a",
        date=date(2026, 9, 1),
        account_id="chk",
        category="Uncategorized",
        subcategory="",
        description="To savings",
        amount=Decimal("-50.00"),
        type=TransactionType.EXPENSE,
        transfer_id=None,
        notes=None,
        counterparty_account="2222",
    )
    inflow = Transaction(
        id="b",
        date=date(2026, 9, 1),
        account_id="sav",
        category="Uncategorized",
        subcategory="",
        description="From checking",
        amount=Decimal("50.00"),
        type=TransactionType.INCOME,
        transfer_id=None,
        notes=None,
    )
    write_ledger([outflow, inflow], config.LEDGER_PATH)
    return outflow, inflow


def test_detect_transfers_preview_finds_matching_pair(client):
    _seed_transfer_candidates(client)

    response = client.get("/import/detect-transfers/preview")

    assert response.status_code == 200
    assert "Found 1 transaction pair" in response.text
    assert "To savings" in response.text
    assert "From checking" in response.text


def test_detect_transfers_preview_shows_no_candidates_message(client):
    write_accounts(
        [
            Account(
                id="chk", name="Checking", number="1111", starting_balance=Decimal("0")
            )
        ],
        config.ACCOUNTS_PATH,
    )

    response = client.get("/import/detect-transfers/preview")

    assert "No candidate transfers found" in response.text


def test_detect_transfers_apply_merges_selected_pair_into_a_transfer(client):
    outflow, inflow = _seed_transfer_candidates(client)
    preview = client.get("/import/detect-transfers/preview")
    matches_payload = _extract_hidden_value(preview.text, "matches_payload")

    response = client.post(
        "/import/detect-transfers/apply",
        data={"matches_payload": matches_payload, "selected": [outflow.id]},
    )

    assert "Merged 1 transaction pair into transfers" in response.headers["hx-trigger"]
    ledger = {t.id: t for t in read_ledger(config.LEDGER_PATH)}
    assert ledger[outflow.id].type is TransactionType.TRANSFER
    assert ledger[inflow.id].type is TransactionType.TRANSFER
    assert ledger[outflow.id].transfer_id == ledger[inflow.id].transfer_id
    assert ledger[outflow.id].amount == Decimal("-50.00")
    assert ledger[inflow.id].amount == Decimal("50.00")


def test_detect_transfers_apply_skips_an_unselected_pair(client):
    outflow, inflow = _seed_transfer_candidates(client)
    preview = client.get("/import/detect-transfers/preview")
    matches_payload = _extract_hidden_value(preview.text, "matches_payload")

    response = client.post(
        "/import/detect-transfers/apply",
        data={"matches_payload": matches_payload},
    )

    assert "Merged 0 transaction pairs into transfers" in response.headers["hx-trigger"]
    ledger = {t.id: t for t in read_ledger(config.LEDGER_PATH)}
    assert ledger[outflow.id].type is TransactionType.EXPENSE
    assert ledger[inflow.id].type is TransactionType.INCOME
