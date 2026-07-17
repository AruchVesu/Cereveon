"""Account data export — GDPR Articles 15 & 20 (access + portability).

``export_player_data`` serialises every row the erasure plan covers
into one JSON-ready document.  Scope parity with erasure is structural,
not aspirational: both consumers read the SAME plan
(``erasure.player_data_plan``), so the metadata-discovery tripwire in
``test_auth_account_deletion.py`` transitively guarantees that a new
player-linked table lands in the export the moment it lands in the
erasure plan.  ``test_auth_data_export.py`` adds the read-side pins
(secrets policy, cross-player isolation, end-to-end serialisability).

Column policy
-------------
Every column is exported EXCEPT the per-table exclusions below.
Credentials are not "personal data provided by the data subject"
(Art. 20's scope), and exporting password/token hashes would only hand
out offline-attack material.  The pattern guard in the tests forces any
future column whose name smells like a secret (password / token /
secret) into this policy explicitly — it cannot leak into the export by
default.

Layering: models + the erasure plan only — no routers, no services, no
engine code (same auth-directory sweep as erasure.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.erasure import player_data_plan
from llm.seca.auth.models import Base

#: Bump when the document shape changes (key renames, nesting changes).
#: Adding tables or columns is NOT a version bump — consumers must
#: treat ``data`` as an open mapping.
EXPORT_VERSION = 1

#: Columns withheld from the export, per table.  Keep this to genuine
#: secrets — the default posture is "export everything" so the document
#: stays a faithful Art. 15 disclosure.
COLUMN_EXCLUSIONS: dict[str, frozenset[str]] = {
    "players": frozenset({"password_hash"}),
    # previous_token_expires_at is not itself a secret (it is the grace
    # window's end for the withheld previous_token_hash), but exporting
    # the timestamp without its hash is meaningless — the whole rotation
    # triple stays internal.
    "sessions": frozenset({"token_hash", "previous_token_hash", "previous_token_expires_at"}),
}


def _jsonable(value: Any) -> Any:
    """Datetimes become ISO-8601 strings; everything else the SECA
    schema stores (str/int/float/bool/None + JSON-column dicts/lists)
    is already JSON-native and passes through untouched."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _serialize_row(model: type[Base], row: Base) -> dict[str, Any]:
    excluded = COLUMN_EXCLUSIONS.get(model.__tablename__, frozenset())
    return {
        column.name: _jsonable(getattr(row, column.key))
        for column in model.__table__.columns
        if column.name not in excluded
    }


def export_player_data(db: DBSession, player_id: str) -> dict[str, Any]:
    """Build the full Art. 15/20 export document for one player.

    Read-only — one SELECT per player-linked table (the erasure plan's
    grandchild criteria reuse their IN-subqueries unchanged).  Every
    table appears in ``data`` even when empty (``[]``, never omitted),
    so consumers can rely on the key set matching the erasure scope.
    """
    data: dict[str, list[dict[str, Any]]] = {}
    for model, criterion in player_data_plan(player_id):
        rows = db.query(model).filter(criterion).all()
        data[model.__tablename__] = [_serialize_row(model, row) for row in rows]
    return {
        "export_version": EXPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "player_id": player_id,
        "contents": "GDPR Art. 15/20 data export — every table linked to this account",
        "data": data,
    }
