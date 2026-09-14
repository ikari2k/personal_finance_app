"""Routes for the bank CSV import wizard.

Named ``import_`` (trailing underscore) to avoid shadowing the ``import``
keyword — see ``app.main``'s ``include_router`` call.

A single page (``/import``) whose ``#import-wizard`` content is swapped
step to step via htmx, rather than separate pages per step — there's no
server-side session state anywhere else in this app, so the uploaded
file's bytes are threaded forward as a base64 hidden field across steps
instead of a server-side temp file. Three steps: upload (pick an account,
a bank, and a file) → mapping setup (only the first time a bank is used;
skipped once ``config/import_mappings/<bank>.toml`` exists) → preview
(new/duplicate/filtered-out counts, confirm writes to the ledger).
"""

import base64
import csv
import io
import json
import uuid
from datetime import date as date_
from decimal import Decimal

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.models.import_mapping import ImportMapping
from app.models.transaction import Transaction, TransactionType
from app.routers.htmx_events import toast
from app.services.importer import (
    build_transactions,
    filter_by_account_number,
    find_duplicates,
    parse_rows,
)
from app.services.transactions import ensure_category
from app.storage.accounts import read_accounts
from app.storage.categories import read_categories, write_categories
from app.storage.import_mappings import list_banks, read_mapping, write_mapping
from app.storage.ledger import read_ledger, write_ledger
from app.storage.rules import read_rules
from app.templating import templates

router = APIRouter(prefix="/import", tags=["import"])

ENCODING_CHOICES = ["utf-8", "cp1250", "cp1252", "latin-1"]


def _decode(content: bytes, encoding: str) -> str | None:
    """Decode ``content`` with ``encoding``, or ``None`` if it doesn't fit."""
    try:
        return content.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return None


@router.get("", response_class=HTMLResponse)
def import_page(request: Request) -> HTMLResponse:
    """Render the import page with the step-1 upload form."""
    return templates.TemplateResponse(
        request,
        "import/page.html",
        {"accounts": read_accounts(), "banks": list_banks(), "error": None},
    )


@router.get("/step/upload", response_class=HTMLResponse)
def upload_step(request: Request) -> HTMLResponse:
    """Re-render just the step-1 upload form (used by the "start over" link)."""
    return templates.TemplateResponse(
        request,
        "import/_upload.html",
        {"accounts": read_accounts(), "banks": list_banks(), "error": None},
    )


@router.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    account_id: str = Form(...),
    bank: str = Form(...),
    file: UploadFile = File(...),
) -> HTMLResponse:
    """Handle the uploaded file: go to mapping setup, or straight to preview.

    A bank with no saved mapping yet goes to step 2 (mapping setup); a
    bank that's been imported before reuses its saved mapping and skips
    straight to step 3 (preview) — the auto-reuse behavior Phase 3 is
    meant to demonstrate.
    """
    content = await file.read()
    bank = bank.strip()
    if not bank:
        return templates.TemplateResponse(
            request,
            "import/_upload.html",
            {
                "accounts": read_accounts(),
                "banks": list_banks(),
                "error": "Bank name is required.",
            },
        )
    file_content_b64 = base64.b64encode(content).decode("ascii")
    mapping = read_mapping(bank)
    if mapping is None:
        return _render_mapping_setup(
            request,
            bank=bank,
            account_id=account_id,
            file_content_b64=file_content_b64,
            delimiter=",",
            encoding="utf-8",
            date_format="%Y-%m-%d",
            decimal_separator=".",
            error=None,
        )
    return _render_preview(request, mapping, account_id, content)


def _render_mapping_setup(
    request: Request,
    *,
    bank: str,
    account_id: str,
    file_content_b64: str,
    delimiter: str,
    encoding: str,
    date_format: str,
    decimal_separator: str,
    error: str | None,
) -> HTMLResponse:
    """Render step 2: mapping settings plus a column-picker preview.

    Re-parses the file with whatever settings were submitted so the
    preview reflects the current delimiter/encoding/date format/decimal
    separator; column-index selections aren't preserved across a
    settings change (a different delimiter can change the column count
    entirely, so keeping a stale index would be actively wrong).
    """
    content = base64.b64decode(file_content_b64)
    text = _decode(content, encoding)
    preview_rows: list[list[str]] = []
    if text is None:
        error = (
            error or f"Could not decode the file as {encoding}. Try another encoding."
        )
    else:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        preview_rows = [row for row, _ in zip(reader, range(6), strict=False)]
        if len(preview_rows) < 2:
            error = error or "Couldn't find any data rows with this delimiter."
    return templates.TemplateResponse(
        request,
        "import/_mapping_setup.html",
        {
            "bank": bank,
            "account_id": account_id,
            "file_content_b64": file_content_b64,
            "delimiter": delimiter,
            "encoding": encoding,
            "date_format": date_format,
            "decimal_separator": decimal_separator,
            "encoding_choices": ENCODING_CHOICES,
            "header": preview_rows[0] if preview_rows else [],
            "sample_row": preview_rows[1] if len(preview_rows) > 1 else [],
            "column_indexes": range(len(preview_rows[0])) if preview_rows else [],
            "error": error,
        },
    )


@router.post("/mapping-setup/reparse", response_class=HTMLResponse)
def reparse_mapping_setup(
    request: Request,
    bank: str = Form(...),
    account_id: str = Form(...),
    file_content_b64: str = Form(...),
    delimiter: str = Form(","),
    encoding: str = Form("utf-8"),
    date_format: str = Form("%Y-%m-%d"),
    decimal_separator: str = Form("."),
) -> HTMLResponse:
    """Re-render step 2 after the user changes a parsing setting."""
    return _render_mapping_setup(
        request,
        bank=bank,
        account_id=account_id,
        file_content_b64=file_content_b64,
        delimiter=delimiter,
        encoding=encoding,
        date_format=date_format,
        decimal_separator=decimal_separator,
        error=None,
    )


@router.post("/mapping-setup/save", response_class=HTMLResponse)
def save_mapping_setup(
    request: Request,
    bank: str = Form(...),
    account_id: str = Form(...),
    file_content_b64: str = Form(...),
    delimiter: str = Form(...),
    encoding: str = Form(...),
    date_format: str = Form(...),
    decimal_separator: str = Form(...),
    date_column: int = Form(...),
    description_column: int = Form(...),
    amount_column: int = Form(...),
    account_number_column: str = Form(""),
) -> HTMLResponse:
    """Save the mapping for ``bank`` and proceed straight to the preview step."""
    columns = {
        "date": date_column,
        "description": description_column,
        "amount": amount_column,
    }
    if account_number_column.strip():
        columns["account_number"] = int(account_number_column)
    mapping = ImportMapping(
        bank=bank,
        delimiter=delimiter,
        encoding=encoding,
        date_format=date_format,
        decimal_separator=decimal_separator,
        columns=columns,
    )
    write_mapping(mapping)
    content = base64.b64decode(file_content_b64)
    return _render_preview(request, mapping, account_id, content)


def _render_preview(
    request: Request, mapping: ImportMapping, account_id: str, content: bytes
) -> HTMLResponse:
    """Parse, filter, dedup, and categorize ``content``; render step 3."""
    text = content.decode(mapping.encoding)
    accounts = {account.id: account for account in read_accounts()}
    account = accounts.get(account_id)
    account_number = account.number if account else ""

    rows = parse_rows(text, mapping)
    kept, filtered_count = filter_by_account_number(rows, account_number)
    existing = read_ledger()
    new_rows, duplicate_rows = find_duplicates(kept, existing, account_id)
    rules = read_rules()
    transactions = build_transactions(new_rows, account_id, rules)

    rows_payload = json.dumps(
        [
            {
                "date": txn.date.isoformat(),
                "description": txn.description,
                "amount": str(txn.amount),
                "category": txn.category,
                "subcategory": txn.subcategory,
            }
            for txn in transactions
        ]
    )
    return templates.TemplateResponse(
        request,
        "import/_preview.html",
        {
            "account": account,
            "account_id": account_id,
            "transactions": transactions,
            "new_count": len(new_rows),
            "duplicate_count": len(duplicate_rows),
            "filtered_count": filtered_count,
            "rows_payload": rows_payload,
        },
    )


@router.post("/confirm", response_class=HTMLResponse)
def confirm(
    request: Request,
    account_id: str = Form(...),
    rows_payload: str = Form(...),
) -> HTMLResponse:
    """Write the previewed transactions to the ledger."""
    rows = json.loads(rows_payload)
    categories = read_categories()
    new_transactions = []
    for row in rows:
        amount = Decimal(row["amount"])
        txn_type = TransactionType.EXPENSE if amount < 0 else TransactionType.INCOME
        categories = ensure_category(
            categories, txn_type, row["category"], row["subcategory"]
        )
        new_transactions.append(
            Transaction(
                id=str(uuid.uuid4()),
                date=date_.fromisoformat(row["date"]),
                account_id=account_id,
                category=row["category"],
                subcategory=row["subcategory"],
                description=row["description"],
                amount=amount,
                type=txn_type,
                transfer_id=None,
                notes=None,
            )
        )
    write_categories(categories)
    write_ledger([*read_ledger(), *new_transactions])
    return templates.TemplateResponse(
        request,
        "import/_done.html",
        {"count": len(new_transactions)},
        headers=toast(f"Imported {len(new_transactions)} transactions"),
    )
