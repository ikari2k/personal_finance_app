"""Routes for managing auto-categorization rules and bulk reclassification.

Rules have no natural unique key of their own (``pattern`` isn't
guaranteed unique — two rules could share one with different
priorities), so edit/delete address a rule by its position in
``config/rules.toml`` rather than inventing an id field beyond
CLAUDE.md's finalized schema. Single-user, no concurrent editors, so an
index shifting under a stale link between page loads isn't a real risk
in practice.
"""

import json
from datetime import date as date_

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.models.category import CategoriesByType
from app.models.rule import Rule
from app.routers.htmx_events import toast
from app.services.categorizer import (
    ReclassificationChange,
    apply_reclassification,
    compile_pattern,
    plan_reclassification,
)
from app.services.transactions import ensure_category
from app.storage.accounts import read_accounts
from app.storage.categories import read_categories, write_categories
from app.storage.ledger import read_ledger, write_ledger
from app.storage.rules import read_rules, write_rules
from app.templating import templates

router = APIRouter(prefix="/rules", tags=["rules"])


def _category_names(categories: CategoriesByType) -> list[str]:
    """All known category names across both trees, for the rule form's autocomplete.

    A rule has no ``type`` of its own (it matches on description text
    alone, see ``app.models.rule``), so its category isn't scoped to
    income or expense the way a transaction's is — the datalist merges
    both trees rather than picking one.
    """
    names: set[str] = set()
    for tree in categories.values():
        names.update(tree.keys())
    return sorted(names)


def _subcategory_names(categories: CategoriesByType) -> list[str]:
    """All known subcategory names across both trees, for the same datalist reason."""
    names: set[str] = set()
    for tree in categories.values():
        for entry in tree.values():
            names.update(entry["subcategories"].keys())
    return sorted(names)


def _render_table(
    request: Request, *, oob: bool = False, headers: dict[str, str] | None = None
) -> HTMLResponse:
    """Render the rules table, ordered as stored (matches edit/delete indices)."""
    return templates.TemplateResponse(
        request,
        "rules/_table.html",
        {"rules": list(enumerate(read_rules())), "oob": oob},
        headers=headers,
    )


def _render_form(
    request: Request,
    *,
    index: int | None,
    values: dict[str, str],
    error: str | None = None,
) -> HTMLResponse:
    """Render the shared add-or-edit rule form fragment."""
    categories = read_categories()
    return templates.TemplateResponse(
        request,
        "rules/_form.html",
        {
            "action_url": f"/rules/{index}" if index is not None else "/rules",
            "editing": index is not None,
            "values": values,
            "category_names": _category_names(categories),
            "subcategory_names": _subcategory_names(categories),
            "error": error,
        },
    )


def _parse_priority(raw: str) -> int:
    """Parse a raw priority form field; blank means ``0``."""
    raw = raw.strip()
    try:
        return int(raw) if raw else 0
    except ValueError as exc:
        raise ValueError("priority must be a whole number") from exc


def _validate_rule(pattern: str, category: str, priority: str) -> int:
    """Validate a submitted rule; returns the parsed priority or raises ``ValueError``.

    Regex compile-checked here, at save time — CLAUDE.md's "regex rules
    must fail loudly" invariant, extended from Phase 3's apply-time-only
    check so a bad pattern never reaches ``config/rules.toml`` at all.
    """
    compile_pattern(pattern)
    if not category.strip():
        raise ValueError("category is required")
    return _parse_priority(priority)


@router.get("", response_class=HTMLResponse)
def list_rules(request: Request) -> HTMLResponse:
    """Render the rules management page."""
    return templates.TemplateResponse(
        request, "rules/list.html", {"rules": list(enumerate(read_rules()))}
    )


@router.get("/new", response_class=HTMLResponse)
def new_rule_form(request: Request) -> HTMLResponse:
    """Render the "add rule" form for the dialog."""
    return _render_form(
        request,
        index=None,
        values={"pattern": "", "category": "", "subcategory": "", "priority": "0"},
    )


@router.get("/{index}/edit", response_class=HTMLResponse)
def edit_rule_form(request: Request, index: int) -> HTMLResponse:
    """Render the "edit rule" form for the dialog."""
    rules = read_rules()
    if index < 0 or index >= len(rules):
        return HTMLResponse("Rule not found", status_code=404)
    rule = rules[index]
    return _render_form(
        request,
        index=index,
        values={
            "pattern": rule.pattern,
            "category": rule.category,
            "subcategory": rule.subcategory,
            "priority": str(rule.priority),
        },
    )


@router.post("", response_class=HTMLResponse)
def create_rule(
    request: Request,
    pattern: str = Form(...),
    category: str = Form(...),
    subcategory: str = Form(""),
    priority: str = Form("0"),
) -> HTMLResponse:
    """Create a new rule; close the dialog and refresh the table on success."""
    values = {
        "pattern": pattern,
        "category": category,
        "subcategory": subcategory,
        "priority": priority,
    }
    try:
        priority_value = _validate_rule(pattern, category, priority)
    except ValueError as exc:
        return _render_form(request, index=None, values=values, error=str(exc))
    rules = read_rules()
    rules.append(
        Rule(
            pattern=pattern,
            category=category,
            subcategory=subcategory,
            priority=priority_value,
        )
    )
    write_rules(rules)
    return _render_table(
        request, oob=True, headers=toast("Rule added", close_dialog=True)
    )


@router.post("/{index}", response_class=HTMLResponse)
def update_rule(
    request: Request,
    index: int,
    pattern: str = Form(...),
    category: str = Form(...),
    subcategory: str = Form(""),
    priority: str = Form("0"),
) -> HTMLResponse:
    """Update an existing rule; close the dialog and refresh the table on success."""
    values = {
        "pattern": pattern,
        "category": category,
        "subcategory": subcategory,
        "priority": priority,
    }
    rules = read_rules()
    if index < 0 or index >= len(rules):
        return HTMLResponse("Rule not found", status_code=404)
    try:
        priority_value = _validate_rule(pattern, category, priority)
    except ValueError as exc:
        return _render_form(request, index=index, values=values, error=str(exc))
    rules[index] = Rule(
        pattern=pattern,
        category=category,
        subcategory=subcategory,
        priority=priority_value,
    )
    write_rules(rules)
    return _render_table(
        request, oob=True, headers=toast("Rule updated", close_dialog=True)
    )


@router.post("/{index}/delete", response_class=HTMLResponse)
def delete_rule(request: Request, index: int) -> HTMLResponse:
    """Delete a rule by its position in the file and refresh the table."""
    rules = read_rules()
    if 0 <= index < len(rules):
        del rules[index]
        write_rules(rules)
    return _render_table(request, headers=toast("Rule deleted"))


@router.get("/reclassify/preview", response_class=HTMLResponse)
def preview_reclassification(request: Request) -> HTMLResponse:
    """Compute and render the full before/after diff a rule run would make.

    Read-only, per CLAUDE.md's "bulk reclassification always previews
    first" invariant. The change list is threaded to the apply step as a
    hidden JSON field — the same no-server-session pattern the import
    wizard uses for its ``rows_payload`` — so apply reuses exactly what
    was previewed rather than recomputing against a ledger that may have
    moved on in between.
    """
    accounts = {account.id: account for account in read_accounts()}
    changes = plan_reclassification(read_ledger(), read_rules())
    changes_payload = json.dumps(
        [
            {
                "transaction_id": change.transaction_id,
                "date": change.date.isoformat(),
                "account_id": change.account_id,
                "description": change.description,
                "old_category": change.old_category,
                "old_subcategory": change.old_subcategory,
                "new_category": change.new_category,
                "new_subcategory": change.new_subcategory,
            }
            for change in changes
        ]
    )
    return templates.TemplateResponse(
        request,
        "rules/_reclassify_preview.html",
        {
            "changes": changes,
            "accounts": accounts,
            "changes_payload": changes_payload,
        },
    )


@router.post("/reclassify/apply", response_class=HTMLResponse)
def apply_reclassification_route(
    request: Request, changes_payload: str = Form(...)
) -> HTMLResponse:
    """Write the previewed reclassification to the ledger.

    Rebuilds ``ReclassificationChange`` objects from the previewed
    payload rather than recomputing the plan, so what gets written can
    never disagree with what the user saw in preview (see
    ``preview_reclassification``). Any new category/subcategory a rule
    introduces is added to ``config/categories.toml`` on the fly, same
    as import-time categorization (``app.routers.import_.confirm``).
    """
    raw_changes = json.loads(changes_payload)
    changes = [
        ReclassificationChange(
            transaction_id=raw["transaction_id"],
            date=date_.fromisoformat(raw["date"]),
            account_id=raw["account_id"],
            description=raw["description"],
            old_category=raw["old_category"],
            old_subcategory=raw["old_subcategory"],
            new_category=raw["new_category"],
            new_subcategory=raw["new_subcategory"],
        )
        for raw in raw_changes
    ]

    ledger = read_ledger()
    by_id = {txn.id: txn for txn in ledger}
    categories = read_categories()
    for change in changes:
        txn = by_id.get(change.transaction_id)
        if txn is None:
            continue
        categories = ensure_category(
            categories, txn.type, change.new_category, change.new_subcategory
        )
    write_categories(categories)
    write_ledger(apply_reclassification(ledger, changes))

    count = len(changes)
    return HTMLResponse(
        "",
        headers=toast(
            f"Reclassified {count} transaction{'' if count == 1 else 's'}",
            close_dialog=True,
        ),
    )
