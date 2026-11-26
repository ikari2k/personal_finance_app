"""Routes for the payee management view.

Payees are the clean names of who was paid (``Transaction.payee``); this page
helps name them in bulk. Unnamed transactions are clustered by what their bank
text says, the user names a cluster, and the name is saved on every unnamed
row of that cluster and remembered (``config/payee_aliases.toml``) so imports
name those spellings too. Renaming and merging change existing payees.

Every change is previewed in a dialog first, from the same computation the
apply step runs, so the preview cannot disagree with what is written.
``description`` is never touched, and rows that already have a payee are never
overwritten by naming a cluster.
"""

import json
from collections.abc import Callable
from urllib.parse import quote, unquote, urlparse

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.routers.htmx_events import toast
from app.services.payee_logos import MAX_LOGO_BYTES
from app.services.payees import (
    CARD_SPELLINGS,
    PayeeSummary,
    add_rules,
    apply_naming,
    clean_payee_name,
    clear_skipped,
    combinations,
    coverage,
    default_labels,
    distinct_spellings,
    field_options,
    field_text,
    group_members,
    merge_payees,
    money_flow,
    payee_field_labels,
    payee_summaries,
    plan_naming,
    retarget_rules,
    skip_group,
    split_group,
    split_key,
    suggest_name,
    unnamed_groups,
    unsplit_group,
    without_payee_fields,
)
from app.storage.import_mappings import list_all_mappings
from app.storage.ledger import read_ledger, write_ledger
from app.storage.payee_logos import (
    carry_logos,
    delete_logo,
    logo_path,
    logo_url,
    save_logo,
)
from app.storage.payees import read_payee_book, write_payee_book
from app.templating import templates

router = APIRouter(prefix="/payees", tags=["payees"])

QUEUE_LIMIT = 20  # groups listed under "Name these"
QUEUE_STEP = 20  # more groups each "Show more" adds
GOAL_GROUPS = 25  # the meter's marker: coverage after naming this many groups
PREVIEW_ROWS = 6  # sample transactions shown in a preview dialog
LOGO_FILTERS = ("with", "without")  # list filter on having a logo
COMBO_LIMIT = 25  # combinations listed in a group's workspace


def _transactions(count: int) -> str:
    """Return ``"1 transaction"`` / ``"3 transactions"`` for toast messages."""
    return f"{count} transaction{'' if count == 1 else 's'}"


_SORTS: dict[str, Callable[[PayeeSummary], object]] = {
    "name": lambda p: p.name.casefold(),
    "count": lambda p: p.count,
    "spent": lambda p: p.spent,
    "received": lambda p: p.received,
    "last": lambda p: p.last_seen,
}
TABS = ("single", "multiple")  # "Name these": one spelling vs several
DEFAULT_TAB = "single"
TAB_COOKIE = "payee_tab"
FIELD_COOKIE = "payee_field"
DEFAULT_SORT = "name"  # when nothing was ever chosen
SORT_COOKIE = "payee_sort"
DIR_COOKIE = "payee_dir"
SORT_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year
_DEFAULT_DIR = {
    "name": "asc",
    "count": "desc",
    "spent": "desc",
    "received": "desc",
    "last": "desc",
}


def resolve_sort(
    request: Request, sort: str | None, direction: str | None
) -> tuple[str, str]:
    """Pick the list's ``(sort, direction)``: the URL, else the remembered choice.

    Same convention as the Transactions filters: a value in the URL always wins,
    an absent one falls back to the cookie, and anything unknown falls back to
    the default (name, A to Z). A new sort given without a direction takes that
    sort's own natural direction instead of inheriting the old column's.
    """
    if sort in _SORTS:
        chosen = sort
        remembered_dir = None
    else:
        cookie_sort = request.cookies.get(SORT_COOKIE)
        chosen = cookie_sort if cookie_sort in _SORTS else DEFAULT_SORT
        remembered_dir = request.cookies.get(DIR_COOKIE)
    for candidate in (direction, remembered_dir):
        if candidate in ("asc", "desc"):
            return chosen, candidate
    return chosen, _DEFAULT_DIR[chosen]


def resolve_tab(request: Request, tab: str | None) -> str:
    """Pick the "Name these" tab: the URL, else the remembered one, else the first."""
    if tab in TABS:
        return tab
    cookie = request.cookies.get(TAB_COOKIE)
    return cookie if cookie in TABS else DEFAULT_TAB


def _remember_sort(
    response: HTMLResponse,
    sort: str,
    direction: str,
    tab: str = DEFAULT_TAB,
    field: str = "",
) -> HTMLResponse:
    """Store the list's sort and the queue's tab and field for the next visit."""
    response.set_cookie(SORT_COOKIE, sort, max_age=SORT_COOKIE_MAX_AGE)
    response.set_cookie(DIR_COOKIE, direction, max_age=SORT_COOKIE_MAX_AGE)
    response.set_cookie(TAB_COOKIE, tab, max_age=SORT_COOKIE_MAX_AGE)
    if field:
        # Labels are free text ("Tytuł"), but a cookie value must be plain ASCII.
        response.set_cookie(FIELD_COOKIE, quote(field), max_age=SORT_COOKIE_MAX_AGE)
    return response


def remembered_field(request: Request) -> str:
    """Return the payee field last chosen for "Name these", or ``""``."""
    return unquote(request.cookies.get(FIELD_COOKIE, ""))


def _parse_combo(raw: str) -> dict[str, str] | None:
    """Parse the ``combo`` form value: a JSON object of field label -> value.

    Anything else (blank, malformed, empty, non-text values) means "no combo".
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not data:
        return None
    if not all(
        isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in data.items()
    ):
        return None
    return data


def _group_detail_context(
    ledger: list, key: str, labels: list[str], chosen: str
) -> dict | None:
    """Everything a group's workspace shows, or ``None`` if the group is gone.

    Every field the group's rows have (with its values), the full list of
    spellings it was formed on, and the distinct combinations of the ticked
    fields, each with a suggested name.
    """
    members = group_members(ledger, key)
    if not members:
        return None
    label = split_key(key)[0]
    picked, error = _chosen_labels(key, members, labels, chosen)
    combos, without = combinations(members, picked) if picked else ([], len(members))
    rows = []
    for combo in combos[:COMBO_LIMIT]:
        suggestion = next(
            (s for v in combo.values.values() if (s := suggest_name([(v, 1)]))), ""
        )
        rows.append(
            {
                "combo": combo,
                "json": json.dumps(combo.values, ensure_ascii=False),
                "suggestion": suggestion,
            }
        )
    return {
        "key": key,
        "label": label,
        "options": field_options(members),
        "picked": picked,
        "error": error,
        "rows": rows,
        "more_combos": max(0, len(combos) - COMBO_LIMIT),
        "without": without,
        "member_count": len(members),
        "spellings": distinct_spellings([field_text(t, label) for t in members]),
    }


def _content_context(
    q: str = "",
    sort: str = DEFAULT_SORT,
    direction: str = "",
    open_key: str = "",
    open_labels: list[str] | None = None,
    tab: str = DEFAULT_TAB,
    field: str = "",
    limit: int = QUEUE_LIMIT,
    logo: str = "",
) -> dict:
    """Everything ``payees/_content.html`` renders, from the ledger and the book.

    ``logo`` narrows the list to payees ``"with"`` or ``"without"`` a logo.

    ``field`` picks which payee field "Name these" builds its suggestions from
    (the first one when it is blank or unknown); ``tab`` which of them it lists:
    groups of one spelling, or groups of several.

    ``open_key`` names a group whose workspace should come back already
    expanded (with ``open_labels`` ticked), e.g. right after one of its
    combinations was named.
    """
    sort = sort if sort in _SORTS else DEFAULT_SORT
    direction = direction if direction in ("asc", "desc") else _DEFAULT_DIR[sort]
    ledger = read_ledger()
    book = read_payee_book()
    named, total = coverage(ledger)
    configured = [f.label for m in list_all_mappings() for f in m.payee_fields]
    labels = payee_field_labels(ledger, configured)
    field = field if field in labels else (labels[0] if labels else "")
    by_field = {label: unnamed_groups(ledger, book, label) for label in labels}
    groups = by_field.get(field, [])
    singles = [g for g in groups if len(g.spellings) == 1]
    multiples = [g for g in groups if len(g.spellings) > 1]
    tab = tab if tab in TABS else DEFAULT_TAB
    shown = singles if tab == "single" else multiples
    limit = max(QUEUE_LIMIT, min(limit, len(shown)))
    queue = shown[:limit]
    goal_named = named + sum(g.count for g in groups[:GOAL_GROUPS])
    needle = q.strip().casefold()
    summaries = [
        s
        for s in payee_summaries(ledger)
        if not needle
        or needle in s.name.casefold()
        or any(needle in spelling.casefold() for spelling, _ in s.spellings)
    ]
    logo = logo if logo in LOGO_FILTERS else ""
    if logo:
        summaries = [s for s in summaries if bool(logo_url(s.name)) == (logo == "with")]
    summaries.sort(key=_SORTS[sort], reverse=(direction == "desc"))
    return {
        "named": named,
        "total": total,
        "pct": (named / total * 100) if total else 0.0,
        "goal_pct": (goal_named / total * 100) if total else 0.0,
        "queue": queue,
        "field": field,
        "field_tabs": [
            {
                "label": label,
                "groups": len(by_field[label]),
                "rows": sum(g.count for g in by_field[label]),
            }
            for label in labels
        ],
        "tab": tab,
        "card_spellings": CARD_SPELLINGS,
        "no_fields_count": without_payee_fields(ledger),
        "single_count": len(singles),
        "multiple_count": len(multiples),
        "queue_cover": sum(g.count for g in queue),
        "more_groups": len(shown) - len(queue),
        "queue_limit": limit,
        "queue_step": QUEUE_STEP,
        "more_once": sum(1 for g in shown[len(queue) :] if g.count == 1),
        "skipped_count": len(book.skipped),
        "summaries": summaries,
        "q": q,
        "logo": logo,
        "sort": sort,
        "dir": direction,
        "default_dir": _DEFAULT_DIR,
        "has_payees": any(t.payee for t in ledger),
        "open_key": open_key,
        "detail": (
            _group_detail_context(ledger, open_key, open_labels or [], "1")
            if open_key and open_labels
            else None
        ),
    }


def _current_view(request: Request) -> str:
    """Which payee page an HTMX request came from: ``"name"`` or ``"list"``.

    Actions on either page re-render ``#payees-content`` out of band, and that
    must be the sections the page being looked at has.
    """
    path = urlparse(request.headers.get("hx-current-url", "")).path.rstrip("/")
    return "name" if path.endswith("/payees/suggestions") else "list"


def _render_content(
    request: Request,
    *,
    oob: bool = False,
    headers: dict[str, str] | None = None,
    open_key: str = "",
    open_labels: list[str] | None = None,
) -> HTMLResponse:
    """Render the swappable page region (coverage, queue, list) afresh.

    The list keeps the sort, and the queue the tab, the user last chose (read
    from their cookies).
    """
    sort, direction = resolve_sort(request, None, None)
    context = _content_context(
        sort=sort,
        direction=direction,
        open_key=open_key,
        open_labels=open_labels,
        tab=resolve_tab(request, None),
        field=remembered_field(request),
    )
    return templates.TemplateResponse(
        request,
        "payees/_content.html",
        {**context, "oob": oob, "view": _current_view(request)},
        headers=headers,
    )


@router.get("", response_class=HTMLResponse)
def payees_page(
    request: Request,
    q: str = "",
    sort: str | None = None,
    direction: str | None = Query(None, alias="dir"),
    part: str = "",
    tab: str | None = None,
    field: str | None = None,
    limit: int = QUEUE_LIMIT,
    logo: str = "",
) -> HTMLResponse:
    """Render the payee management page, or just a part of it.

    ``part=table`` is the list (search and sorting), ``part=queue`` the "Name
    these" box (switching field or tab). The sort, the tab and the field are
    remembered between visits (see ``resolve_sort`` and ``resolve_tab``).
    ``limit`` is how many suggestions the box lists ("Show more" raises it).
    """
    sort, direction = resolve_sort(request, sort, direction)
    tab = resolve_tab(request, tab)
    wanted_field = field if field else remembered_field(request)
    context = _content_context(
        q, sort, direction, tab=tab, field=wanted_field, limit=limit, logo=logo
    )
    if part == "table":
        response = templates.TemplateResponse(request, "payees/_table.html", context)
    elif part == "queue":
        response = templates.TemplateResponse(request, "payees/_queue.html", context)
    else:
        response = templates.TemplateResponse(
            request, "payees/list.html", {**context, "oob": False, "view": "list"}
        )
    return _remember_sort(response, sort, direction, tab, context["field"])


@router.get("/suggestions", response_class=HTMLResponse)
def suggestions_page(
    request: Request,
    tab: str | None = None,
    field: str | None = None,
    limit: int = QUEUE_LIMIT,
) -> HTMLResponse:
    """Render the "Name these" page: suggestions per payee field, with progress."""
    sort, direction = resolve_sort(request, None, None)
    tab = resolve_tab(request, tab)
    wanted_field = field if field else remembered_field(request)
    context = _content_context(
        "", sort, direction, tab=tab, field=wanted_field, limit=limit
    )
    response = templates.TemplateResponse(
        request, "payees/suggestions.html", {**context, "oob": False, "view": "name"}
    )
    return _remember_sort(response, sort, direction, tab, context["field"])


@router.get("/group", response_class=HTMLResponse)
def group_workspace(
    request: Request,
    key: str,
    fields: list[str] = Query(default=[]),
    chosen: str = "",
) -> HTMLResponse:
    """Render a group's workspace: every field's values, all spellings, combinations."""
    detail = _group_detail_context(read_ledger(), key, fields, chosen)
    return templates.TemplateResponse(
        request, "payees/_group_detail.html", {"detail": detail}
    )


def _chosen_labels(
    key: str, members: list, labels: list[str], chosen: str
) -> tuple[list[str], str | None]:
    """Return the fields a naming rule uses, and an error if none are usable.

    Before the user has touched the checkboxes (``chosen`` empty) the defaults
    apply. Afterwards exactly the ticked fields do, limited to the ones the
    group's rows actually have.
    """
    available = {o.label for o in field_options(members) if o.present}
    if not chosen:
        return default_labels(key, members), None
    picked = [label for label in labels if label in available]
    if not picked:
        return [], "Pick at least one field to build the rule from."
    return picked, None


def _name_dialog(
    request: Request,
    key: str,
    name: str,
    labels: list[str],
    chosen: str,
    combo: dict[str, str] | None = None,
    *,
    error: str | None = None,
) -> HTMLResponse:
    """Render the "name this" dialog: what it would change, and the fields it uses.

    With ``combo`` it names exactly one combination of field values (one rule on
    all of them); without, the whole group, with fields the user can tick.
    """
    ledger = read_ledger()
    members = group_members(ledger, key)
    if combo:
        picked, label_error = list(combo), None
    else:
        picked, label_error = _chosen_labels(key, members, labels, chosen)
    error = error or label_error
    cleaned = name
    if error is None:
        try:
            cleaned = clean_payee_name(name)
        except ValueError as exc:
            error = str(exc)
    if error is None and not members:
        error = "Nothing left to name in this group; it may have been named already."
    plan = (
        plan_naming(ledger, read_payee_book(), key, picked, cleaned, only=combo)
        if picked and cleaned and members
        else None
    )
    if error is None and plan is not None and not plan.rows:
        error = (
            "Nothing would be named: these rows are already named or claimed by "
            "a more specific rule."
            if combo
            else "These fields would not name any transaction. Pick different fields."
        )
    rows = plan.rows if plan else []
    spent, received = money_flow(rows)
    return templates.TemplateResponse(
        request,
        "payees/_name_dialog.html",
        {
            "key": key,
            "name": cleaned,
            "options": field_options(members),
            "picked": picked,
            "combo": combo,
            "combo_json": json.dumps(combo, ensure_ascii=False) if combo else "",
            "rule_count": len(plan.rules) if plan else 0,
            "count": len(rows),
            "without_fields": plan.rows_without_fields if plan else 0,
            "spent": spent,
            "received": received,
            "sample": sorted(rows, key=lambda t: t.date, reverse=True)[:PREVIEW_ROWS],
            "error": error,
        },
    )


@router.get("/name-preview", response_class=HTMLResponse)
def name_preview(
    request: Request,
    key: str,
    name: str = "",
    fields: list[str] = Query(default=[]),
    chosen: str = "",
    combo: str = "",
) -> HTMLResponse:
    """Preview naming a group (or one combination): its rules and the rows it names."""
    return _name_dialog(request, key, name, fields, chosen, _parse_combo(combo))


@router.post("/name", response_class=HTMLResponse)
def name_group_route(
    request: Request,
    key: str = Form(...),
    name: str = Form(""),
    fields: list[str] = Form(default=[]),
    chosen: str = Form(""),
    combo: str = Form(""),
) -> HTMLResponse:
    """Name a group or one combination: save its rules and name the rows they claim."""
    only = _parse_combo(combo)
    try:
        payee = clean_payee_name(name)
    except ValueError as exc:
        return _name_dialog(request, key, name, fields, chosen, only, error=str(exc))
    ledger = read_ledger()
    book = read_payee_book()
    members = group_members(ledger, key)
    if only:
        picked, label_error = list(only), None
    else:
        picked, label_error = _chosen_labels(key, members, fields, chosen)
    if label_error or not members:
        return _name_dialog(request, key, name, fields, chosen, only)
    plan = plan_naming(ledger, book, key, picked, payee, only=only)
    if not plan.rows:
        return _name_dialog(request, key, name, fields, chosen, only)
    write_ledger(apply_naming(ledger, plan, payee))
    write_payee_book(add_rules(book, plan.rules))
    return _render_content(
        request,
        oob=True,
        headers=toast(
            f"Named {_transactions(len(plan.rows))} as {payee}", close_dialog=True
        ),
        # Splitting a group means naming several combinations in a row, so its
        # workspace stays open on the same fields.
        open_key=key if only else "",
        open_labels=list(only) if only else None,
    )


@router.post("/split", response_class=HTMLResponse)
def split_route(request: Request, key: str = Form(...)) -> HTMLResponse:
    """Divide a group into one proposal per spelling (they join the single tab)."""
    ledger = read_ledger()
    book = read_payee_book()
    label = split_key(key)[0]
    count = len({field_text(t, label) for t in group_members(ledger, key)})
    write_payee_book(split_group(book, key))
    return _render_content(
        request,
        oob=True,
        headers=toast(f"Divided into {count} single spellings"),
    )


@router.post("/unsplit", response_class=HTMLResponse)
def unsplit_route(request: Request, key: str = Form(...)) -> HTMLResponse:
    """Group a divided spelling with its similar ones again."""
    write_payee_book(unsplit_group(read_payee_book(), key))
    return _render_content(request, oob=True, headers=toast("Grouped with similar"))


@router.post("/skip", response_class=HTMLResponse)
def skip_route(request: Request, key: str = Form(...)) -> HTMLResponse:
    """Leave a group unnamed: hide it from the "Name these" queue."""
    write_payee_book(skip_group(read_payee_book(), key))
    return _render_content(request, oob=True, headers=toast("Left unnamed"))


@router.post("/unskip", response_class=HTMLResponse)
def unskip_route(request: Request) -> HTMLResponse:
    """Bring every group left unnamed back into the queue."""
    write_payee_book(clear_skipped(read_payee_book()))
    return _render_content(request, oob=True, headers=toast("Groups restored"))


def _rename_dialog(
    request: Request, old: str, new: str, *, error: str | None = None
) -> HTMLResponse:
    """Render the rename form for payee ``old`` with ``new`` typed in."""
    count = sum(1 for t in read_ledger() if t.payee == old)
    return templates.TemplateResponse(
        request,
        "payees/_rename_dialog.html",
        {"old": old, "new": new, "count": count, "error": error},
    )


@router.get("/rename", response_class=HTMLResponse)
def rename_form(request: Request, name: str) -> HTMLResponse:
    """Render the rename dialog for one payee."""
    return _rename_dialog(request, name, name)


@router.post("/rename", response_class=HTMLResponse)
def rename_route(
    request: Request, old: str = Form(...), new: str = Form("")
) -> HTMLResponse:
    """Rename a payee on every transaction, and in the remembered spellings."""
    try:
        payee = clean_payee_name(new)
    except ValueError as exc:
        return _rename_dialog(request, old, new, error=str(exc))
    if payee == old:
        return _rename_dialog(request, old, new, error="That is already its name.")
    updated, count = merge_payees(read_ledger(), [old], payee)
    write_ledger(updated)
    write_payee_book(retarget_rules(read_payee_book(), [old], payee))
    carry_logos([old], payee)
    return _render_content(
        request,
        oob=True,
        headers=toast(f"Renamed {_transactions(count)} to {payee}", close_dialog=True),
    )


def _merge_dialog(
    request: Request, names: list[str], name: str, *, error: str | None = None
) -> HTMLResponse:
    """Render the merge preview for ``names`` becoming ``name``."""
    ledger = read_ledger()
    sources = set(names)
    moved = sum(1 for t in ledger if t.payee in sources and t.payee != name)
    return templates.TemplateResponse(
        request,
        "payees/_merge_dialog.html",
        {"names": names, "name": name, "moved": moved, "error": error},
    )


@router.post("/merge-preview", response_class=HTMLResponse)
def merge_preview(
    request: Request, names: list[str] = Form(default=[]), name: str = Form("")
) -> HTMLResponse:
    """Preview merging the ticked payees into one name."""
    if len(names) < 2:
        return _merge_dialog(request, names, name, error="Tick at least two payees.")
    error = None
    try:
        name = clean_payee_name(name)
    except ValueError as exc:
        error = str(exc)
    return _merge_dialog(request, names, name, error=error)


@router.post("/merge", response_class=HTMLResponse)
def merge_route(
    request: Request, names: list[str] = Form(default=[]), name: str = Form("")
) -> HTMLResponse:
    """Merge the ticked payees into one, on every transaction and spelling."""
    if len(names) < 2:
        return _merge_dialog(request, names, name, error="Tick at least two payees.")
    try:
        payee = clean_payee_name(name)
    except ValueError as exc:
        return _merge_dialog(request, names, name, error=str(exc))
    updated, count = merge_payees(read_ledger(), names, payee)
    write_ledger(updated)
    book = retarget_rules(read_payee_book(), names, payee)
    write_payee_book(book)
    carry_logos(names, payee)
    return _render_content(
        request,
        oob=True,
        headers=toast(
            f"Merged into {payee}: {_transactions(count)}", close_dialog=True
        ),
    )


def _logo_dialog(
    request: Request, name: str, *, error: str | None = None
) -> HTMLResponse:
    """Render the add/replace/remove logo dialog for payee ``name``."""
    return templates.TemplateResponse(
        request,
        "payees/_logo_dialog.html",
        {"name": name, "has_logo": bool(logo_url(name)), "error": error},
    )


@router.get("/logo", response_class=HTMLResponse)
def logo_form(request: Request, name: str) -> HTMLResponse:
    """Render the logo dialog for one payee."""
    return _logo_dialog(request, name)


@router.get("/logo/{filename}")
def logo_file(filename: str) -> Response:
    """Serve a stored logo. Only well-formed logo file names ever resolve.

    SVGs are served under a sandboxing policy so a script in one could never run
    if the file is opened on its own.
    """
    path = logo_path(filename)
    if path is None:
        return Response(status_code=404)
    return FileResponse(
        path,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; sandbox"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/logo", response_class=HTMLResponse)
def logo_upload(
    request: Request, name: str = Form(...), file: UploadFile = File(...)
) -> HTMLResponse:
    """Save an uploaded image as payee ``name``'s logo."""
    if not any(t.payee == name for t in read_ledger()):
        return _logo_dialog(request, name, error="No transaction has that payee.")
    try:
        save_logo(name, file.file.read(MAX_LOGO_BYTES + 1))
    except ValueError as exc:
        return _logo_dialog(request, name, error=str(exc))
    return _render_content(
        request, oob=True, headers=toast(f"Logo saved for {name}", close_dialog=True)
    )


@router.post("/logo/remove", response_class=HTMLResponse)
def logo_remove(request: Request, name: str = Form(...)) -> HTMLResponse:
    """Remove payee ``name``'s logo."""
    delete_logo(name)
    return _render_content(
        request, oob=True, headers=toast(f"Logo removed for {name}", close_dialog=True)
    )
