"""Routes for bank CSV import: mapping creation, editing, and running.

Named ``import_`` (trailing underscore) to avoid shadowing the ``import``
keyword — see ``app.main``'s ``include_router`` call.

``GET /import`` is a dashboard, not a wizard: a "Create new mapping"
entry point, the list of saved mappings (each with Edit/Delete/Run row
actions), and the import history log. Three separate flows branch off
it, each its own full page hosting a ``#import-wizard`` div that swaps
through its steps via htmx (no server-side session state, matching the
rest of the app — an uploaded file's bytes are threaded forward as a
base64 hidden field rather than a temp file):

- **Create** (`/import/new`): upload a file + pick an account + name a
  *new* bank → mapping setup (column picker) → preview → confirm. The
  bank name must be unique — this flow only ever creates a mapping, it
  never reuses or edits an existing one.
- **Edit** (`/import/mappings/{bank}/edit`): no file needed — shows the
  saved settings as plain editable fields. "Save" persists them
  directly, no validation beyond the field types. "Dry run" parses an
  uploaded file against the (possibly unsaved-edited) settings and
  shows a read-only preview — never a path to actually writing the
  ledger; that's what Run is for.
- **Run** (`/import/mappings/{bank}/run`): upload a file + pick an
  account, parse it against the *saved* mapping straight to a normal
  (write-capable) preview → confirm. This is how a mapping gets reused
  for a later import, now that creation no longer doubles as reuse.
"""

import base64
import csv
import io
import json
import uuid
from datetime import date as date_
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.models.import_history import ImportHistoryEntry
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
from app.storage.import_history import append_history_entry, read_history
from app.storage.import_mappings import (
    delete_mapping,
    list_all_mappings,
    read_mapping,
    write_mapping,
)
from app.storage.ledger import read_ledger, write_ledger
from app.storage.rules import read_rules
from app.templating import templates

router = APIRouter(prefix="/import", tags=["import"])

ENCODING_CHOICES = ["utf-8", "cp1250", "cp1252", "latin-1"]


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _decode(content: bytes, encoding: str) -> str | None:
    """Decode ``content`` with ``encoding``, or ``None`` if it doesn't fit."""
    try:
        return content.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return None


def _to_column_index(value: str) -> int | None:
    """Parse a submitted column-index form value; blank/missing means "none"."""
    return int(value) if value.strip().isdigit() else None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def _render_dashboard_content(
    request: Request, *, headers: dict[str, str] | None = None
) -> HTMLResponse:
    """Render the mappings-list + history fragment, shared with delete's refresh."""
    return templates.TemplateResponse(
        request,
        "import/_mappings_content.html",
        {"mappings": list_all_mappings(), "history": read_history()},
        headers=headers,
    )


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Render the import dashboard: create button, mappings list, history."""
    return templates.TemplateResponse(
        request,
        "import/page.html",
        {"mappings": list_all_mappings(), "history": read_history()},
    )


@router.post("/mappings/{bank}/delete", response_class=HTMLResponse)
def delete_mapping_route(request: Request, bank: str) -> HTMLResponse:
    """Delete a saved mapping and refresh the list.

    Import history is untouched — see ``storage.import_mappings
    .delete_mapping``'s docstring on why that's safe.
    """
    delete_mapping(bank)
    return _render_dashboard_content(request, headers=toast("Mapping deleted"))


# ---------------------------------------------------------------------------
# Create flow: /import/new -> mapping-setup -> preview -> confirm
# ---------------------------------------------------------------------------


@router.get("/new", response_class=HTMLResponse)
def new_mapping_page(request: Request) -> HTMLResponse:
    """Render the "create a new mapping" page (step 1: file/account/name)."""
    return templates.TemplateResponse(
        request,
        "import/new.html",
        {"accounts": read_accounts(), "error": None},
    )


@router.get("/new/restart", response_class=HTMLResponse)
def restart_new_mapping(request: Request) -> HTMLResponse:
    """Re-render just the step-1 form (the create flow's "start over" link)."""
    return templates.TemplateResponse(
        request,
        "import/_new_upload.html",
        {"accounts": read_accounts(), "error": None},
    )


@router.post("/new", response_class=HTMLResponse)
async def create_mapping_upload(
    request: Request,
    account_id: str = Form(...),
    bank: str = Form(...),
    file: UploadFile = File(...),
) -> HTMLResponse:
    """Handle the create flow's upload: reject a duplicate name, else go to setup.

    This flow only ever creates a *new* mapping — reusing an existing
    one is what the mapping list's Run icon is for instead, so a name
    collision here is an error, not a silent reuse.
    """
    bank = bank.strip()
    error = None
    if not bank:
        error = "Bank name is required."
    elif read_mapping(bank) is not None:
        error = (
            f'A mapping named "{bank}" already exists. '
            "Edit or run it from the list below instead."
        )
    if error:
        return templates.TemplateResponse(
            request,
            "import/_new_upload.html",
            {"accounts": read_accounts(), "error": error},
        )
    content = await file.read()
    file_content_b64 = base64.b64encode(content).decode("ascii")
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
        restart_url="/import/new/restart",
    )


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
    restart_url: str,
    selected_date_column: int | None = None,
    selected_description_column: int | None = None,
    selected_description_fallback_column: int | None = None,
    selected_amount_column: int | None = None,
    selected_account_number_column: int | None = None,
) -> HTMLResponse:
    """Render the mapping-setup fragment: settings plus a column-picker preview.

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
    that only apply to other transaction types, and none of them can
    ever be a usable mapping target. Scans the whole file, not just the
    preview sample, since a column could easily be blank in the first
    few rows but populated later.

    The currently-selected date/amount columns are also live-parsed
    against the current date_format/decimal_separator and shown as a
    ✓/✗ hint plus a blank-count warning — the wrong column or the wrong
    format string is by far the most common way this step fails, and
    this catches it before "Save mapping" instead of after.
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
            "restart_url": restart_url,
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


@router.post("/mapping-setup/reparse", response_class=HTMLResponse)
def reparse_mapping_setup(
    request: Request,
    bank: str = Form(...),
    account_id: str = Form(...),
    file_content_b64: str = Form(...),
    restart_url: str = Form("/import/new/restart"),
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
    """Re-render mapping setup after the user changes a setting or column pick."""
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
        restart_url=restart_url,
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
    restart_url: str = Form("/import/new/restart"),
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
    column, ...) would be worse than just failing here. On failure,
    every already-made choice (settings and column selections) is
    preserved in the re-rendered form, so only the one wrong setting
    needs fixing.
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
            restart_url=restart_url,
            selected_date_column=date_column,
            selected_description_column=description_column,
            selected_description_fallback_column=description_fallback_idx,
            selected_amount_column=amount_column,
            selected_account_number_column=account_number_idx,
        )
    write_mapping(mapping)
    return _render_preview(
        request, mapping, account_id, content, restart_url=restart_url
    )


# ---------------------------------------------------------------------------
# Preview + confirm (shared by the create and run flows)
# ---------------------------------------------------------------------------


def _render_preview(
    request: Request,
    mapping: ImportMapping,
    account_id: str,
    content: bytes,
    *,
    restart_url: str,
    dry_run: bool = False,
) -> HTMLResponse:
    """Parse, filter, dedup, and categorize ``content``; render the preview.

    ``dry_run=True`` (used by the edit flow) omits the confirm form
    entirely — it's a read-only "what would happen" view, never a path
    to actually writing the ledger. The Run flow's normal preview keeps
    the confirm form, same as the create flow's.
    """
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
            "bank": mapping.bank,
            "transactions": transactions,
            "new_count": len(new_rows),
            "duplicate_count": len(duplicate_rows),
            "filtered_count": filtered_count,
            "blank_date_count": blank_date,
            "blank_amount_count": blank_amount,
            "total_rows": total_rows,
            "rows_payload": rows_payload,
            "restart_url": restart_url,
            "dry_run": dry_run,
        },
    )


@router.post("/confirm", response_class=HTMLResponse)
def confirm(
    request: Request,
    account_id: str = Form(...),
    bank: str = Form(...),
    duplicate_count: int = Form(0),
    filtered_count: int = Form(0),
    rows_payload: str = Form(...),
    restart_url: str = Form("/import/new/restart"),
) -> HTMLResponse:
    """Write the previewed transactions to the ledger and log the import."""
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

    accounts = {account.id: account for account in read_accounts()}
    account = accounts.get(account_id)
    append_history_entry(
        ImportHistoryEntry(
            timestamp=datetime.now(),
            bank=bank,
            account_id=account_id,
            account_name=account.name if account else account_id,
            new_count=len(new_transactions),
            duplicate_count=duplicate_count,
            filtered_count=filtered_count,
        )
    )

    return templates.TemplateResponse(
        request,
        "import/_done.html",
        {"count": len(new_transactions), "restart_url": restart_url},
        headers=toast(f"Imported {len(new_transactions)} transactions"),
    )


# ---------------------------------------------------------------------------
# Run flow: reuse a saved mapping against a new file
# ---------------------------------------------------------------------------


@router.get("/mappings/{bank}/run", response_class=HTMLResponse)
def run_mapping_page(request: Request, bank: str) -> HTMLResponse:
    """Render the Run flow's upload prompt (account + file for a known bank).

    Full page on a plain navigation (from the mapping list's Run icon);
    just the form fragment when re-fetched via htmx (the preview's
    "start over" link, within the same page).
    """
    mapping = read_mapping(bank)
    if mapping is None:
        return HTMLResponse(f'No saved mapping named "{bank}".', status_code=404)
    context = {"bank": mapping.bank, "accounts": read_accounts(), "error": None}
    template = "import/_run_upload.html" if _is_htmx(request) else "import/run.html"
    return templates.TemplateResponse(request, template, context)


@router.post("/mappings/{bank}/run", response_class=HTMLResponse)
async def run_mapping(
    request: Request,
    bank: str,
    account_id: str = Form(...),
    file: UploadFile = File(...),
) -> HTMLResponse:
    """Parse an uploaded file against ``bank``'s saved mapping and preview it."""
    mapping = read_mapping(bank)
    if mapping is None:
        return HTMLResponse(f'No saved mapping named "{bank}".', status_code=404)
    content = await file.read()
    return _render_preview(
        request,
        mapping,
        account_id,
        content,
        restart_url=f"/import/mappings/{quote(bank, safe='')}/run",
    )


# ---------------------------------------------------------------------------
# Edit flow: change a saved mapping's settings, with an optional dry run
# ---------------------------------------------------------------------------


def _render_edit_form(
    request: Request,
    mapping: ImportMapping,
    *,
    error: str | None = None,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    """Render the file-less edit-settings fragment for ``mapping``."""
    return templates.TemplateResponse(
        request,
        "import/_edit_form.html",
        {
            "mapping": mapping,
            "accounts": read_accounts(),
            "encoding_choices": ENCODING_CHOICES,
            "error": error,
        },
        headers=headers,
    )


@router.get("/mappings/{bank}/edit", response_class=HTMLResponse)
def edit_mapping_page(request: Request, bank: str) -> HTMLResponse:
    """Render the edit-mapping page (full page or fragment, see run_mapping_page)."""
    mapping = read_mapping(bank)
    if mapping is None:
        return HTMLResponse(f'No saved mapping named "{bank}".', status_code=404)
    if _is_htmx(request):
        return _render_edit_form(request, mapping)
    return templates.TemplateResponse(
        request,
        "import/edit_mapping.html",
        {
            "mapping": mapping,
            "accounts": read_accounts(),
            "encoding_choices": ENCODING_CHOICES,
            "error": None,
        },
    )


def _mapping_from_edit_form(
    bank: str,
    *,
    delimiter: str,
    encoding: str,
    date_format: str,
    decimal_separator: str,
    date_column: int,
    description_column: int,
    description_fallback_column: str,
    amount_column: int,
    account_number_column: str,
) -> ImportMapping:
    columns = {
        "date": date_column,
        "description": description_column,
        "amount": amount_column,
    }
    if account_number_column.strip():
        columns["account_number"] = int(account_number_column)
    if description_fallback_column.strip():
        columns["description_fallback"] = int(description_fallback_column)
    return ImportMapping(
        bank=bank,
        delimiter=delimiter,
        encoding=encoding,
        date_format=date_format,
        decimal_separator=decimal_separator,
        columns=columns,
    )


@router.post("/mappings/{bank}/edit", response_class=HTMLResponse)
def save_edited_mapping(
    request: Request,
    bank: str,
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
    """Save the edited settings directly — no file, no parse validation.

    Dry run (below) is the tool for validating a change before
    committing it; Save just persists whatever's currently typed.
    """
    mapping = _mapping_from_edit_form(
        bank,
        delimiter=delimiter,
        encoding=encoding,
        date_format=date_format,
        decimal_separator=decimal_separator,
        date_column=date_column,
        description_column=description_column,
        description_fallback_column=description_fallback_column,
        amount_column=amount_column,
        account_number_column=account_number_column,
    )
    write_mapping(mapping)
    return _render_edit_form(request, mapping, headers=toast("Mapping saved"))


@router.post("/mappings/{bank}/dry-run", response_class=HTMLResponse)
async def dry_run_mapping(
    request: Request,
    bank: str,
    account_id: str = Form(...),
    file: UploadFile | None = File(None),
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
    """Parse a file against the (possibly unsaved) edited settings, read-only.

    Never writes anything — the preview it renders has no confirm form
    (see ``_render_preview``'s ``dry_run``). Tests the form's *current*
    values, not necessarily what's saved on disk, so a change can be
    validated before committing it via Save.
    """
    mapping = _mapping_from_edit_form(
        bank,
        delimiter=delimiter,
        encoding=encoding,
        date_format=date_format,
        decimal_separator=decimal_separator,
        date_column=date_column,
        description_column=description_column,
        description_fallback_column=description_fallback_column,
        amount_column=amount_column,
        account_number_column=account_number_column,
    )
    if file is None or not file.filename:
        return _render_edit_form(
            request, mapping, error="Choose a CSV file to dry-run against."
        )
    content = await file.read()
    return _render_preview(
        request,
        mapping,
        account_id,
        content,
        restart_url=f"/import/mappings/{quote(bank, safe='')}/edit",
        dry_run=True,
    )
