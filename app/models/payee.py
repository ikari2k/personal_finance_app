"""Domain model for the payee book: the rules that name payees automatically."""

from pydantic import BaseModel

# Fields every transaction has, so a rule can always use them. Any other field a
# rule uses is one an import mapping configured (see ``PayeeFieldColumn``).
DESCRIPTION_FIELD = "Description"
COUNTERPARTY_FIELD = "Counterparty account"
BUILT_IN_FIELDS = (DESCRIPTION_FIELD, COUNTERPARTY_FIELD)
MAX_PAYEE_FIELDS = 5  # configurable columns per import mapping


class PayeeRule(BaseModel):
    """If every field in ``fields`` has this value, the payee is ``payee``.

    ``fields`` maps a field label to the exact value to match (case and
    spacing are ignored, nothing else is: no patterns, so a rule can never
    match something unintended). A row missing any of the labels never
    matches. When several rules match one row, the one with more fields wins,
    then the newer one (later in ``PayeeBook.rules``).
    """

    fields: dict[str, str]
    payee: str


class PayeeBook(BaseModel):
    """Everything remembered about payees, stored in ``config/payee_aliases.toml``.

    ``rules`` name payees on import and on existing transactions. ``skipped``
    holds group keys (see ``app.services.payees.group_key``) the user chose to
    leave unnamed, so they stop appearing in the "Name these" queue. ``split``
    holds group keys the user divided: each spelling in them is its own
    proposition instead of one group. ``no_logo`` holds payee names marked as
    having no logo at all, so the list can tell "still to add" from "never".
    """

    rules: list[PayeeRule] = []
    skipped: list[str] = []
    split: list[str] = []
    no_logo: list[str] = []
