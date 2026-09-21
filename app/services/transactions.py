"""Business rules for recording transactions and transfers.

Pure functions over in-memory data — no file I/O. Callers (routers) read
the current ledger/accounts/categories via ``app.storage``, call these to
build and validate new rows, and write the result back afterward.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date as date_
from decimal import Decimal

from app.models.account import Account
from app.models.category import CategoriesByType
from app.models.transaction import Transaction, TransactionType
from app.services.importer import normalize_account_number


def ensure_category(
    categories: CategoriesByType,
    txn_type: TransactionType,
    category: str,
    subcategory: str,
) -> CategoriesByType:
    """Return ``categories`` with ``category``/``subcategory`` added if new.

    Implements the PRD's "create categories on the fly" rule: entering a
    transaction against an unknown category or subcategory adds it rather
    than rejecting the transaction. A blank ``subcategory`` is not added.
    Income and expense have separate trees, keyed by ``txn_type.value``;
    ``txn_type`` must be ``INCOME`` or ``EXPENSE`` (transfers use a fixed
    category outside this tree — see ``new_transfer_pair``).
    """
    if txn_type is TransactionType.TRANSFER:
        raise ValueError("transfers do not use the managed category tree")
    updated = {
        bucket: {
            name: {
                "icon": entry["icon"],
                "budget": entry["budget"],
                "subcategories": dict(entry["subcategories"]),
            }
            for name, entry in tree.items()
        }
        for bucket, tree in categories.items()
    }
    tree = updated.setdefault(txn_type.value, {})
    entry = tree.setdefault(category, {"icon": "", "budget": "", "subcategories": {}})
    if subcategory and subcategory not in entry["subcategories"]:
        entry["subcategories"][subcategory] = {"icon": "", "budget": ""}
    return updated


def _signed_amount(
    account_ids: Iterable[str],
    *,
    account_id: str,
    amount: Decimal,
    type: TransactionType,
) -> Decimal:
    """Validate and sign-normalize an income/expense amount.

    Shared by ``new_transaction`` and ``update_transaction``. Raises
    ``ValueError`` if ``account_id`` isn't known, ``amount`` isn't
    positive, or ``type`` is ``TRANSFER`` (transfers never go through this
    path — see ``new_transfer_pair``).
    """
    if type is TransactionType.TRANSFER:
        raise ValueError("use new_transfer_pair() to record a transfer")
    if account_id not in set(account_ids):
        raise ValueError(f"unknown account_id '{account_id}'")
    if amount <= 0:
        raise ValueError("amount must be positive")
    return -amount if type is TransactionType.EXPENSE else amount


def new_transaction(
    account_ids: Iterable[str],
    *,
    account_id: str,
    date: date_,
    category: str,
    subcategory: str,
    description: str,
    amount: Decimal,
    type: TransactionType,
    notes: str | None = None,
) -> Transaction:
    """Build a new income/expense ``Transaction``.

    ``amount`` is the unsigned magnitude the user entered; it's normalized
    to a signed value here (expenses negative, income positive). Raises
    ``ValueError`` if ``account_id`` isn't a known account, ``amount``
    isn't positive, or ``type`` is ``TRANSFER`` (use ``new_transfer_pair``
    instead, since a transfer is two linked rows, not one).
    """
    signed_amount = _signed_amount(
        account_ids, account_id=account_id, amount=amount, type=type
    )
    return Transaction(
        id=uuid.uuid4().hex,
        date=date,
        account_id=account_id,
        category=category,
        subcategory=subcategory,
        description=description,
        amount=signed_amount,
        type=type,
        transfer_id=None,
        notes=notes,
    )


def update_transaction(
    transactions: list[Transaction],
    transaction_id: str,
    account_ids: Iterable[str],
    *,
    account_id: str,
    date: date_,
    category: str,
    subcategory: str,
    description: str,
    amount: Decimal,
    type: TransactionType,
    notes: str | None = None,
) -> list[Transaction]:
    """Return ``transactions`` with ``transaction_id``'s row replaced.

    Same validation as ``new_transaction`` (unknown account, non-positive
    amount, ``TRANSFER`` type all raise ``ValueError``), but keeps the
    original ``id`` rather than minting a new one. Also raises
    ``ValueError`` if no transaction with ``transaction_id`` exists, or if
    it currently *is* a transfer leg — a transfer's two rows must change
    together or not at all, which this single-row function can't do; use
    ``update_transfer_pair`` instead.
    """
    existing = next((t for t in transactions if t.id == transaction_id), None)
    if existing is None:
        raise ValueError(f"no transaction with id '{transaction_id}'")
    if existing.type is TransactionType.TRANSFER:
        raise ValueError(
            "transfers can't be edited via update_transaction; use update_transfer_pair"
        )

    signed_amount = _signed_amount(
        account_ids, account_id=account_id, amount=amount, type=type
    )
    updated = Transaction(
        id=transaction_id,
        date=date,
        account_id=account_id,
        category=category,
        subcategory=subcategory,
        description=description,
        amount=signed_amount,
        type=type,
        transfer_id=None,
        notes=notes,
    )
    return [updated if t.id == transaction_id else t for t in transactions]


def update_transaction_category(
    transactions: list[Transaction],
    transaction_id: str,
    *,
    category: str,
    subcategory: str,
) -> list[Transaction]:
    """Return ``transactions`` with only ``transaction_id``'s category changed.

    The narrow "recategorize inline from the transactions table" path —
    distinct from ``update_transaction``'s full-row edit, this touches
    nothing else about the row (account, date, amount, description,
    notes all stay exactly as they were). Raises ``ValueError`` if no
    transaction with ``transaction_id`` exists, or it's a transfer leg
    (transfers always use the fixed "Transfer" category — see
    ``new_transfer_pair`` — and aren't recategorizable this way, same
    restriction as ``update_transaction``). Whether ``category``/
    ``subcategory`` is itself a real, existing pair is the caller's job
    (``services.categories.category_pair_exists``) — this function
    doesn't know about the category tree.
    """
    existing = next((t for t in transactions if t.id == transaction_id), None)
    if existing is None:
        raise ValueError(f"no transaction with id '{transaction_id}'")
    if existing.type is TransactionType.TRANSFER:
        raise ValueError("transfers always use a fixed category, never editable here")
    updated = existing.model_copy(
        update={"category": category, "subcategory": subcategory}
    )
    return [updated if t.id == transaction_id else t for t in transactions]


def update_transaction_notes(
    transactions: list[Transaction], transaction_id: str, *, notes: str
) -> list[Transaction]:
    """Return ``transactions`` with only ``transaction_id``'s notes changed.

    The narrow "edit inline from the transactions table" path for
    ``notes``, mirroring ``update_transaction_category`` — touches
    nothing else about the row, including ``description`` itself, which
    stays exactly as it came from import/manual entry (it's what
    ``services.importer``'s duplicate detection and ``services
    .categorizer``'s rule matching key off of; ``notes`` is a separate,
    purely personal field precisely so editing it can never disturb
    either). Allowed on a transfer leg too, unlike category — a personal
    note doesn't interact with the fixed "Transfer" category tree the
    way recategorizing would. Raises ``ValueError`` if no transaction
    with ``transaction_id`` exists.
    """
    existing = next((t for t in transactions if t.id == transaction_id), None)
    if existing is None:
        raise ValueError(f"no transaction with id '{transaction_id}'")
    updated = existing.model_copy(update={"notes": notes or None})
    return [updated if t.id == transaction_id else t for t in transactions]


def update_transfer_pair(
    transactions: list[Transaction],
    transfer_id: str,
    account_ids: Iterable[str],
    *,
    from_account_id: str,
    to_account_id: str,
    date: date_,
    amount: Decimal,
    description: str = "",
    notes: str | None = None,
) -> list[Transaction]:
    """Return ``transactions`` with both legs of ``transfer_id`` replaced.

    The single-row ``update_transaction`` refuses to touch a transfer leg
    (see its docstring) because a transfer is two linked rows that must
    change together or not at all — this is that atomic path: both legs
    are rebuilt from the new field values and swapped in together, never
    just one. Same validation as ``new_transfer_pair`` (unknown account,
    same account on both sides, non-positive amount all raise
    ``ValueError``), but each leg keeps its original ``id`` rather than
    minting new ones. Category/subcategory are carried over from the
    existing legs unchanged (transfers always use the fixed "Transfer"
    category — the edit form doesn't expose it). Raises ``ValueError`` if
    ``transfer_id`` doesn't identify exactly one outflow and one inflow
    row.
    """
    legs = [t for t in transactions if t.transfer_id == transfer_id]
    outflow = next((t for t in legs if t.amount < 0), None)
    inflow = next((t for t in legs if t.amount > 0), None)
    if len(legs) != 2 or outflow is None or inflow is None:
        raise ValueError(f"no transfer with id '{transfer_id}'")

    known_ids = set(account_ids)
    if from_account_id not in known_ids:
        raise ValueError(f"unknown account_id '{from_account_id}'")
    if to_account_id not in known_ids:
        raise ValueError(f"unknown account_id '{to_account_id}'")
    if from_account_id == to_account_id:
        raise ValueError("a transfer must be between two different accounts")
    if amount <= 0:
        raise ValueError("amount must be positive")

    updated_outflow = Transaction(
        id=outflow.id,
        date=date,
        account_id=from_account_id,
        category=outflow.category,
        subcategory=outflow.subcategory,
        description=description,
        amount=-amount,
        type=TransactionType.TRANSFER,
        transfer_id=transfer_id,
        notes=notes,
    )
    updated_inflow = Transaction(
        id=inflow.id,
        date=date,
        account_id=to_account_id,
        category=inflow.category,
        subcategory=inflow.subcategory,
        description=description,
        amount=amount,
        type=TransactionType.TRANSFER,
        transfer_id=transfer_id,
        notes=notes,
    )
    replacements = {outflow.id: updated_outflow, inflow.id: updated_inflow}
    return [replacements.get(t.id, t) for t in transactions]


def remove_transaction(
    transactions: list[Transaction], transaction_id: str
) -> list[Transaction]:
    """Return ``transactions`` with ``transaction_id`` removed.

    A transfer is two linked rows sharing a ``transfer_id`` (see
    ``new_transfer_pair``); deleting only one leg would either leave the
    other orphaned or desync the pair's amounts — exactly what
    ``services.consistency.check_consistency`` flags. So deleting a
    transfer leg removes *both* legs together in this one call; deleting
    a plain income/expense row removes just that row. Raises
    ``ValueError`` if no transaction with ``transaction_id`` exists.
    """
    target = next((t for t in transactions if t.id == transaction_id), None)
    if target is None:
        raise ValueError(f"no transaction with id '{transaction_id}'")
    if target.transfer_id is not None:
        return [t for t in transactions if t.transfer_id != target.transfer_id]
    return [t for t in transactions if t.id != transaction_id]


def new_transfer_pair(
    account_ids: Iterable[str],
    *,
    from_account_id: str,
    to_account_id: str,
    date: date_,
    amount: Decimal,
    category: str = "Transfer",
    subcategory: str = "",
    description: str = "",
    notes: str | None = None,
) -> tuple[Transaction, Transaction]:
    """Build the linked outflow/inflow row pair for a transfer.

    Raises ``ValueError`` if either account is unknown, the two accounts
    are the same, or ``amount`` isn't positive.
    """
    known_ids = set(account_ids)
    if from_account_id not in known_ids:
        raise ValueError(f"unknown account_id '{from_account_id}'")
    if to_account_id not in known_ids:
        raise ValueError(f"unknown account_id '{to_account_id}'")
    if from_account_id == to_account_id:
        raise ValueError("a transfer must be between two different accounts")
    if amount <= 0:
        raise ValueError("amount must be positive")

    transfer_id = uuid.uuid4().hex
    outflow = Transaction(
        id=uuid.uuid4().hex,
        date=date,
        account_id=from_account_id,
        category=category,
        subcategory=subcategory,
        description=description,
        amount=-amount,
        type=TransactionType.TRANSFER,
        transfer_id=transfer_id,
        notes=notes,
    )
    inflow = Transaction(
        id=uuid.uuid4().hex,
        date=date,
        account_id=to_account_id,
        category=category,
        subcategory=subcategory,
        description=description,
        amount=amount,
        type=TransactionType.TRANSFER,
        transfer_id=transfer_id,
        notes=notes,
    )
    return outflow, inflow


@dataclass
class TransferMatch:
    """One candidate pair of existing income/expense rows that look like a transfer.

    Found by ``find_transfer_matches`` cross-referencing a row's
    ``counterparty_account`` (a raw account number captured at CSV-import
    time — see ``Transaction``'s docstring) against every *registered*
    account's own ``Account.number`` — a specific, explicit reference the
    bank itself recorded, not a guess. Both ids keep their original
    ``Transaction.id`` unchanged; ``merge_into_transfer``/
    ``apply_transfer_matches`` are what actually rewrite the ledger.
    """

    from_transaction_id: str
    to_transaction_id: str
    date: date_
    from_account_id: str
    to_account_id: str
    amount: Decimal
    from_description: str
    to_description: str


def find_transfer_matches(
    transactions: Iterable[Transaction],
    accounts: Iterable[Account],
    *,
    max_day_gap: int = 3,
) -> list[TransferMatch]:
    """Find existing income/expense row pairs that are secretly a transfer.

    A row is a match *anchor* when its ``counterparty_account`` normalizes
    (see ``app.services.importer.normalize_account_number``) to another
    registered account's own ``Account.number`` — narrowing the search to
    one specific counterparty account. Pairing to one specific row on
    that account still needs an equal-and-opposite amount within
    ``max_day_gap`` days of the anchor's own date (a transfer's two legs
    routinely post a day or two apart on each side) — the *partner* row
    doesn't need its own ``counterparty_account`` set, since only one
    side of a real-world export reliably names the other account. Ties
    (more than one same-amount row within the window) are broken by the
    closest date. Already-linked transfer rows are never candidates
    either as anchor or partner; each transaction is matched at most
    once, processed in ``(date, id)`` order for determinism. Purely
    additive to ``app.services.importer``'s "imported rows are always
    income or expense" invariant — this runs as a separate, explicit,
    always-previewed step against the ledger as it already stands, never
    at import time.
    """
    account_by_number = {
        normalize_account_number(account.number): account.id
        for account in accounts
        if account.number
    }
    non_transfer = [t for t in transactions if t.type is not TransactionType.TRANSFER]
    by_account: dict[str, list[Transaction]] = defaultdict(list)
    for txn in non_transfer:
        by_account[txn.account_id].append(txn)

    anchors = sorted(
        (
            txn
            for txn in non_transfer
            if txn.counterparty_account
            and txn.counterparty_account in account_by_number
        ),
        key=lambda t: (t.date, t.id),
    )

    matched_ids: set[str] = set()
    matches: list[TransferMatch] = []
    for anchor in anchors:
        if anchor.id in matched_ids:
            continue
        counterparty_account_id = account_by_number[anchor.counterparty_account]
        if counterparty_account_id == anchor.account_id:
            continue
        best: Transaction | None = None
        best_gap = None
        for other in by_account.get(counterparty_account_id, []):
            if other.id in matched_ids or other.id == anchor.id:
                continue
            if other.amount != -anchor.amount:
                continue
            gap = abs((other.date - anchor.date).days)
            if gap > max_day_gap:
                continue
            if best is None or gap < best_gap:
                best, best_gap = other, gap
        if best is None:
            continue
        matched_ids.add(anchor.id)
        matched_ids.add(best.id)
        outflow, inflow = (anchor, best) if anchor.amount < 0 else (best, anchor)
        matches.append(
            TransferMatch(
                from_transaction_id=outflow.id,
                to_transaction_id=inflow.id,
                date=outflow.date,
                from_account_id=outflow.account_id,
                to_account_id=inflow.account_id,
                amount=-outflow.amount,
                from_description=outflow.description,
                to_description=inflow.description,
            )
        )
    return matches


def merge_into_transfer(
    transactions: list[Transaction],
    match: TransferMatch,
    *,
    category: str = "Transfer",
    subcategory: str = "",
) -> list[Transaction]:
    """Return ``transactions`` with ``match``'s two rows linked into one transfer.

    Each row keeps its own ``id``, ``date``, ``account_id``, ``amount``,
    and ``description`` — only ``type``, ``category``/``subcategory``,
    and the new shared ``transfer_id`` change. Raises ``ValueError`` if
    either row no longer exists or is already a transfer leg (deleted or
    merged since the match was computed — mirrors
    ``update_transfer_pair``'s same guard).
    """
    by_id = {t.id: t for t in transactions}
    outflow = by_id.get(match.from_transaction_id)
    inflow = by_id.get(match.to_transaction_id)
    if outflow is None or inflow is None:
        raise ValueError(
            "transfer match references a transaction that no longer exists"
        )
    if (
        outflow.type is TransactionType.TRANSFER
        or inflow.type is TransactionType.TRANSFER
    ):
        raise ValueError("transfer match references a row that's already a transfer")

    transfer_id = uuid.uuid4().hex
    updates = {
        "type": TransactionType.TRANSFER,
        "category": category,
        "subcategory": subcategory,
        "transfer_id": transfer_id,
    }
    replacements = {
        outflow.id: outflow.model_copy(update=updates),
        inflow.id: inflow.model_copy(update=updates),
    }
    return [replacements.get(t.id, t) for t in transactions]


def apply_transfer_matches(
    transactions: list[Transaction], matches: Iterable[TransferMatch]
) -> list[Transaction]:
    """Return ``transactions`` with every one of ``matches`` merged into a transfer.

    Applies ``merge_into_transfer`` one match at a time; a match
    referencing a row that's been deleted or already merged since
    preview is silently skipped rather than failing the whole batch
    (mirrors ``apply_reclassification``'s same stale-reference
    tolerance).
    """
    result = list(transactions)
    for match in matches:
        try:
            result = merge_into_transfer(result, match)
        except ValueError:
            continue
    return result


@dataclass
class OrphanTransferCandidate:
    """One existing row whose counterparty is registered, but unpaired.

    Found by ``find_orphan_transfer_candidates``: the row's
    ``counterparty_account`` resolves to one of the user's own
    registered accounts (same signal ``find_transfer_matches`` trusts),
    but that account has no matching row anywhere in the ledger to pair
    it with — typically because there's no import data for that account
    at all. ``synthesize_and_merge_transfer`` is what actually creates
    the missing leg and merges both into a transfer.
    """

    transaction_id: str
    date: date_
    account_id: str
    counterparty_account_id: str
    amount: Decimal
    description: str


def find_orphan_transfer_candidates(
    transactions: Iterable[Transaction], accounts: Iterable[Account]
) -> list[OrphanTransferCandidate]:
    """Find rows with a registered counterparty but no real row to pair with.

    ``find_transfer_matches`` already finds every pair where *both* legs
    exist in the ledger; this finds the leftover anchors — rows whose
    ``counterparty_account`` is still a registered account, just one
    with no transaction data at all (or none close enough in amount/date
    to have matched). These are candidates for
    ``synthesize_and_merge_transfer``, not ``merge_into_transfer`` — the
    other leg doesn't exist yet and has to be created, which is why this
    is a separate, explicitly-opt-in step from ordinary transfer
    detection (see CLAUDE.md): every one of these rows is a genuinely
    inferred transaction, not one independently observed in any bank
    export.
    """
    transactions = list(transactions)
    account_by_number = {
        normalize_account_number(account.number): account.id
        for account in accounts
        if account.number
    }
    matched_ids: set[str] = set()
    for match in find_transfer_matches(transactions, accounts):
        matched_ids.add(match.from_transaction_id)
        matched_ids.add(match.to_transaction_id)

    candidates = []
    for txn in transactions:
        if (
            txn.type is TransactionType.TRANSFER
            or txn.id in matched_ids
            or not txn.counterparty_account
            or txn.counterparty_account not in account_by_number
        ):
            continue
        counterparty_account_id = account_by_number[txn.counterparty_account]
        if counterparty_account_id == txn.account_id:
            continue
        candidates.append(
            OrphanTransferCandidate(
                transaction_id=txn.id,
                date=txn.date,
                account_id=txn.account_id,
                counterparty_account_id=counterparty_account_id,
                amount=txn.amount,
                description=txn.description,
            )
        )
    return sorted(candidates, key=lambda c: (c.date, c.transaction_id))


def synthesize_and_merge_transfer(
    transactions: list[Transaction], candidate: OrphanTransferCandidate
) -> list[Transaction]:
    """Create ``candidate``'s missing other leg and merge both into a transfer.

    The existing row keeps its own id/date/amount; the new leg is a
    freshly-minted ``Transaction`` on ``counterparty_account_id`` with
    the opposite-sign amount, same date, and the same description (the
    same "one shared description for both legs" convention as
    ``new_transfer_pair``) — then both are linked via
    ``merge_into_transfer``. Raises ``ValueError`` if the existing row no
    longer exists or is already a transfer (a stale candidate — deleted
    or merged since the preview was computed).
    """
    existing = next((t for t in transactions if t.id == candidate.transaction_id), None)
    if existing is None:
        raise ValueError(f"no transaction with id '{candidate.transaction_id}'")
    if existing.type is TransactionType.TRANSFER:
        raise ValueError("transaction is already a transfer")

    is_existing_outflow = existing.amount < 0
    new_leg = Transaction(
        id=uuid.uuid4().hex,
        date=existing.date,
        account_id=candidate.counterparty_account_id,
        category=existing.category,
        subcategory=existing.subcategory,
        description=existing.description,
        amount=-existing.amount,
        type=TransactionType.INCOME if is_existing_outflow else TransactionType.EXPENSE,
        transfer_id=None,
        notes=None,
    )
    match = TransferMatch(
        from_transaction_id=existing.id if is_existing_outflow else new_leg.id,
        to_transaction_id=new_leg.id if is_existing_outflow else existing.id,
        date=existing.date,
        from_account_id=existing.account_id
        if is_existing_outflow
        else new_leg.account_id,
        to_account_id=new_leg.account_id
        if is_existing_outflow
        else existing.account_id,
        amount=abs(existing.amount),
        from_description=existing.description,
        to_description=existing.description,
    )
    return merge_into_transfer([*transactions, new_leg], match)
