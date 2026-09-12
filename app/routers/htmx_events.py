"""Shared helper for the two client-side events routers fire via ``HX-Trigger``.

``close-dialog`` is caught by whichever dialog is open (its own
``hx-on:close-dialog="this.close()"`` listener — see CLAUDE.md's "Every form is a native
<dialog> modal" section) and ``toast`` is caught by the single global listener in
base.html, showing a transient success confirmation. Both are folded into one JSON
``HX-Trigger`` value (rather than a separate header) so the event fires *before* the
response swap — required for delete actions, whose ``hx-swap="outerHTML"`` detaches the
triggering button from the document, which would otherwise break bubbling for an
after-swap/after-settle event.
"""

import json


def toast(message: str, *, close_dialog: bool = False) -> dict[str, str]:
    """Build an ``HX-Trigger`` header for a toast, optionally closing the dialog too."""
    events: dict[str, object] = {"toast": message}
    if close_dialog:
        events["close-dialog"] = True
    return {"HX-Trigger": json.dumps(events)}
