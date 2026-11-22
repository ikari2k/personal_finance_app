"""Domain model for the payee book: which bank spellings were named what."""

from pydantic import BaseModel


class PayeeAlias(BaseModel):
    """One bank spelling the user named: ``spelling`` -> ``payee``.

    ``spelling`` is the bank's own text exactly as it appeared in a
    transaction's ``description``; matching ignores case and runs of
    whitespace (see ``app.services.payees.spelling_key``) but is otherwise
    exact — no patterns, so an alias can never match something unintended.
    """

    spelling: str
    payee: str


class PayeeBook(BaseModel):
    """Everything remembered about payees, stored in ``config/payee_aliases.toml``.

    ``aliases`` give new imports the clean name for spellings already named.
    ``skipped`` holds group keys (see ``app.services.payees.group_key``) the
    user chose to leave unnamed, so they stop appearing in the "Name these"
    queue.
    """

    aliases: list[PayeeAlias] = []
    skipped: list[str] = []
