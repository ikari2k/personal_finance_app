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
    count_blank_column_values,
    filter_by_account_number,
    find_duplicates,
    parse_amount,
    parse_date,
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
    selected_date_column: int | None = None,
    selected_description_column: int | None = None,
    selected_description_fallback_column: int | None = None,
    selected_amount_column: int | None = None,
    selected_account_number_column: int | None = None,
) -> HTMLResponse:
    """Render step 2: mapping settings plus a column-picker preview.

    Re-parses the file with whatever settings were submitted so the
    preview reflects the current delimiter/encoding/date format/decimal
    separator. Column selections are threaded through and preserved on
    every re-render, including a settings change — a wrong delimiter fix
    rarely changes which columns exist, and losing every column choice
    each time one setting gets tweaked was worse than the rare case
    where a selection no longer matches a real column after a settings
    change (the `<select>` just falls back to its default in that case,
    no error).

    Columns that are blank in *every* data row are left out of the
    picker entirely — a real bank export can have dozens of columns
    that only apply to other transaction types (the Credit Agricole
    sample this was designed against has 101 columns, most blank on any
    given row), and none of them can ever be a usable mapping target.
    Scans the whole file, not just the preview sample, since a column
    could easily be blank in the first few rows but populated later.

    The currently-selected date/amount columns are also live-parsed
    against the current date_format/decimal_separator and shown as a
    ✓/✗ hint — the wrong column (a date-only column that's blank for
    some transaction types, e.g., or a foreign-currency amount column
    that's blank for domestic ones) or the wrong format string is by far
    the most common way this step fails, and this catches it before the
    user ever clicks "Save mapping" instead of after.
    """
    content = base64.b64decode(file_content_b64)
    text = _decode(content, encoding)
    columns: list[dict[str, int | str]] = []
    if text is None:
        error = (
            error or f"Could not decode the file as {encoding}. Try another encoding."
        )
    else:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        if len(rows) < 2:
            error = error or "Couldn't find any data rows with this delimiter."
        else:
            header_row, data_rows = rows[0], rows[1:]
            for i, name in enumerate(header_row):
                sample = next(
                    (r[i].strip() for r in data_rows if i < len(r) and r[i].strip()),
                    None,
                )
                if sample is not None:
                    columns.append({"index": i, "header": name, "sample": sample})
            if not columns:
                error = error or "No non-empty columns found — check the delimiter."

    sample_by_index: dict[int, str] = {
        int(c["index"]): str(c["sample"]) for c in columns
    }

    def _blank_warning(column_index: int) -> str:
        blank, total = count_blank_column_values(text or "", delimiter, column_index)
        if not blank:
            return ""
        return f"⚠ blank in {blank} of {total} rows (those rows are skipped). "

    date_preview: str | None = None
    if selected_date_column in sample_by_index:
        sample = sample_by_index[selected_date_column]
        try:
            parsed_date = parse_date(sample, date_format)
        except ValueError:
            date_preview = f"✗ “{sample}” doesn't match this format"
        else:
            date_preview = f"✓ parses as {parsed_date.isoformat()}"
        date_preview = _blank_warning(selected_date_column) + date_preview
    amount_preview: str | None = None
    if selected_amount_column in sample_by_index:
        sample = sample_by_index[selected_amount_column]
        try:
            parsed_amount = parse_amount(sample, decimal_separator)
        except ValueError:
            amount_preview = f"✗ “{sample}” doesn't parse as an amount"
        else:
            amount_preview = f"✓ parses as {parsed_amount}"
        amount_preview = _blank_warning(selected_amount_column) + amount_preview

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
            "columns": columns,
            "selected_date_column": selected_date_column,
            "selected_description_column": selected_description_column,
            "selected_description_fallback_column": (
                selected_description_fallback_column
            ),
            "selected_amount_column": selected_amount_column,
            "selected_account_number_column": selected_account_number_column,
            "date_preview": date_preview,
            "amount_preview": amount_preview,
            "error": error,
        },
    )


def _to_column_index(value: str) -> int | None:
    """Parse a submitted column-select value; blank/missing means "none"."""
    return int(value) if value.strip().isdigit() else None


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
    date_column: str = Form(""),
    description_column: str = Form(""),
    description_fallback_column: str = Form(""),
    amount_column: str = Form(""),
    account_number_column: str = Form(""),
) -> HTMLResponse:
    """Re-render step 2 after the user changes a parsing setting or column pick."""
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
        selected_date_column=_to_column_index(date_column),
        selected_description_column=_to_column_index(description_column),
        selected_description_fallback_column=_to_column_index(
            description_fallback_column
        ),
        selected_amount_column=_to_column_index(amount_column),
        selected_account_number_column=_to_column_index(account_number_column),
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
    description_fallback_column: str = Form(""),
    amount_column: int = Form(...),
    account_number_column: str = Form(""),
) -> HTMLResponse:
    """Validate the mapping against the file, then save it and show the preview.

    Parsing is attempted *before* ``write_mapping`` — persisting a
    mapping that doesn't actually parse (wrong date format, wrong
    column, ...) would be worse than just failing here: every later
    import from this bank reuses a saved mapping automatically and skips
    setup entirely, so a bad mapping would fail the same way with no
    obvious way back into the setup form to fix it. On failure, every
    already-made choice (settings and column selections) is preserved
    in the re-rendered form, so only the one wrong setting needs fixing.
    """
    columns = {
        "date": date_column,
        "description": description_column,
        "amount": amount_column,
    }
    account_number_idx = (
        int(account_number_column) if account_number_column.strip() else None
    )
    if account_number_idx is not None:
        columns["account_number"] = account_number_idx
    description_fallback_idx = (
        int(description_fallback_column)
        if description_fallback_column.strip()
        else None
    )
    if description_fallback_idx is not None:
        columns["description_fallback"] = description_fallback_idx
    mapping = ImportMapping(
        bank=bank,
        delimiter=delimiter,
        encoding=encoding,
        date_format=date_format,
        decimal_separator=decimal_separator,
        columns=columns,
    )
    content = base64.b64decode(file_content_b64)
    try:
        text = content.decode(encoding)
        parse_rows(text, mapping)
    except (ValueError, LookupError) as exc:
        return _render_mapping_setup(
            request,
            bank=bank,
            account_id=account_id,
            file_content_b64=file_content_b64,
            delimiter=delimiter,
            encoding=encoding,
            date_format=date_format,
            decimal_separator=decimal_separator,
            error=str(exc),
            selected_date_column=date_column,
            selected_description_column=description_column,
            selected_description_fallback_column=description_fallback_idx,
            selected_amount_column=amount_column,
            selected_account_number_column=account_number_idx,
        )
    write_mapping(mapping)
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

    blank_date, total_rows = count_blank_column_values(
        text, mapping.delimiter, mapping.columns["date"]
    )
    blank_amount, _ = count_blank_column_values(
        text, mapping.delimiter, mapping.columns["amount"]
    )

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
            "blank_date_count": blank_date,
            "blank_amount_count": blank_amount,
            "total_rows": total_rows,
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
