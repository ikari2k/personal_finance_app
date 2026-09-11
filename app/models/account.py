"""Domain model for a financial account."""

from decimal import Decimal

from pydantic import BaseModel


class Account(BaseModel):
    """A single account tracked by the ledger.

    ``id`` is a stable short code that never changes even if ``name`` or
    ``description`` does — transactions reference ``id``, never the name.
    """

    id: str
    name: str
    number: str
    description: str = ""
    starting_balance: Decimal
