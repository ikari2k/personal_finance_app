"""Domain model for one past import run.

Written once per confirmed import (``app.routers.import_.confirm``) and
never edited afterward — a plain activity log, not configuration. Each
entry is a self-contained snapshot (bank name, account name) rather than
a live reference to the mapping/account, so deleting a saved mapping or
renaming/deleting an account later never invalidates or breaks a past
history entry.
"""

from datetime import datetime

from pydantic import BaseModel


class ImportHistoryEntry(BaseModel):
    """A record of one completed import: when, from where, into what, and the counts."""

    timestamp: datetime
    bank: str
    account_id: str
    account_name: str
    new_count: int
    duplicate_count: int
    filtered_count: int
