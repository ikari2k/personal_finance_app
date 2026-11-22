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

from collections.abc import Callable

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse

from app.models.payee import PayeeBook
from app.routers.htmx_events import toast
from app.services.payees import (
    PayeeSummary,
    add_aliases,
    clean_payee_name,
    clear_skipped,
    coverage,
    group_members,
    merge_payees,
    money_flow,
    name_group,
    payee_summaries,
    retarget_aliases,
    skip_group,
    unnamed_groups,
)
from app.storage.ledger import read_ledger, write_ledger
from app.storage.payees import read_payee_book, write_payee_book
from app.templating import templates

router = APIRouter(prefix="/payees", tags=["payees"])

QUEUE_LIMIT = 8  # groups listed under "Name these"
GOAL_GROUPS = 25  # the meter's marker: coverage after naming this many groups
PREVIEW_ROWS = 6  # sample transactions shown in a preview dialog


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


def _remember_sort(response: HTMLResponse, sort: str, direction: str) -> HTMLResponse:
    """Store the list's sort so the next visit, and every swap, keeps it."""
    response.set_cookie(SORT_COOKIE, sort, max_age=SORT_COOKIE_MAX_AGE)
    response.set_cookie(DIR_COOKIE, direction, max_age=SORT_COOKIE_MAX_AGE)
    return response


def _content_context(
    q: str = "", sort: str = DEFAULT_SORT, direction: str = ""
) -> dict:
    """Everything ``payees/_content.html`` renders, from the ledger and the book."""
    sort = sort if sort in _SORTS else DEFAULT_SORT
    direction = direction if direction in ("asc", "desc") else _DEFAULT_DIR[sort]
    ledger = read_ledger()
    book = read_payee_book()
    named, total = coverage(ledger)
    groups = unnamed_groups(ledger, book)
    queue = groups[:QUEUE_LIMIT]
    goal_named = named + sum(g.count for g in groups[:GOAL_GROUPS])
    needle = q.strip().casefold()
    summaries = [
        s
        for s in payee_summaries(ledger)
        if not needle
        or needle in s.name.casefold()
        or any(needle in spelling.casefold() for spelling, _ in s.spellings)
    ]
    summaries.sort(key=_SORTS[sort], reverse=(direction == "desc"))
    return {
        "named": named,
        "total": total,
        "pct": (named / total * 100) if total else 0.0,
        "goal_pct": (goal_named / total * 100) if total else 0.0,
        "queue": queue,
        "queue_cover": sum(g.count for g in queue),
        "more_groups": len(groups) - len(queue),
        "more_once": sum(1 for g in groups[len(queue) :] if g.count == 1),
        "skipped_count": len(book.skipped),
        "summaries": summaries,
        "q": q,
        "sort": sort,
        "dir": direction,
        "default_dir": _DEFAULT_DIR,
        "has_payees": any(t.payee for t in ledger),
    }


def _render_content(
    request: Request, *, oob: bool = False, headers: dict[str, str] | None = None
) -> HTMLResponse:
    """Render the swappable page region (coverage, queue, list) afresh.

    The list keeps the sort the user last chose (read from the cookie).
    """
    sort, direction = resolve_sort(request, None, None)
    return templates.TemplateResponse(
        request,
        "payees/_content.html",
        {**_content_context(sort=sort, direction=direction), "oob": oob},
        headers=headers,
    )


@router.get("", response_class=HTMLResponse)
def payees_page(
    request: Request,
    q: str = "",
    sort: str | None = None,
    direction: str | None = Query(None, alias="dir"),
    part: str = "",
) -> HTMLResponse:
    """Render the payee management page, or just its list for search and sorting.

    The sort is remembered between visits (see ``resolve_sort``), so leaving the
    page and coming back, or searching, keeps the order last chosen.
    """
    sort, direction = resolve_sort(request, sort, direction)
    context = _content_context(q, sort, direction)
    if part == "table":
        response = templates.TemplateResponse(request, "payees/_table.html", context)
    else:
        response = templates.TemplateResponse(
            request, "payees/list.html", {**context, "oob": False}
        )
    return _remember_sort(response, sort, direction)


def _name_dialog(
    request: Request, key: str, name: str, *, error: str | None = None
) -> HTMLResponse:
    """Render the "name this group" preview dialog for group ``key`` and ``name``."""
    ledger = read_ledger()
    members = group_members(ledger, key)
    spellings = sorted({" ".join(t.description.split()) for t in members})
    cleaned = name
    if error is None:
        try:
            cleaned = clean_payee_name(name)
        except ValueError as exc:
            error = str(exc)
    if error is None and not members:
        error = "Nothing left to name in this group; it may have been named already."
    return templates.TemplateResponse(
        request,
        "payees/_name_dialog.html",
        {
            "key": key,
            "name": cleaned,
            "count": len(members),
            "spelling_count": len(spellings),
            "spent": money_flow(members)[0],
            "received": money_flow(members)[1],
            "sample": sorted(members, key=lambda t: t.date, reverse=True)[
                :PREVIEW_ROWS
            ],
            "error": error,
        },
    )


@router.get("/name-preview", response_class=HTMLResponse)
def name_preview(request: Request, key: str, name: str = "") -> HTMLResponse:
    """Preview naming a group: how many transactions and spellings it changes."""
    return _name_dialog(request, key, name)


@router.post("/name", response_class=HTMLResponse)
def name_group_route(
    request: Request, key: str = Form(...), name: str = Form("")
) -> HTMLResponse:
    """Name a group's payee-less transactions and remember its spellings."""
    try:
        payee = clean_payee_name(name)
    except ValueError as exc:
        return _name_dialog(request, key, name, error=str(exc))
    ledger = read_ledger()
    spellings = sorted({t.description for t in group_members(ledger, key)})
    updated, count = name_group(ledger, key, payee)
    if not count:
        return _name_dialog(request, key, name)
    write_ledger(updated)
    write_payee_book(add_aliases(read_payee_book(), spellings, payee))
    return _render_content(
        request,
        oob=True,
        headers=toast(f"Named {_transactions(count)} as {payee}", close_dialog=True),
    )


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
    write_payee_book(retarget_aliases(read_payee_book(), [old], payee))
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
    book: PayeeBook = retarget_aliases(read_payee_book(), names, payee)
    write_payee_book(book)
    return _render_content(
        request,
        oob=True,
        headers=toast(
            f"Merged into {payee}: {_transactions(count)}", close_dialog=True
        ),
    )
