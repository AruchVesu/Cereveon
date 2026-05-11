"""``/repertoire`` HTTP surface.

Extracted from ``llm/server.py`` in the Sprint 4.1 server-py-split PR.
The behaviour is unchanged from the pre-split form — every endpoint
preserves its rate-limit, auth dependency, validator, and response
shape. The only change is the routing scope: routes now register on a
local ``APIRouter`` that ``server.py`` mounts via
``app.include_router(...)``.

Owned data:
  - ``DEFAULT_REPERTOIRE`` — the four canonical openings a fresh user
    sees in ``GET /repertoire`` before they've saved anything.  Mirrored
    1-for-1 by ``OpeningsActivity.DEFAULT_REPERTOIRE`` on the Android
    client; drift here will produce a different list on first launch
    than the user sees after their first edit.
  - ``RepertoireEntryRequest`` / ``DrillResultRequest`` — Pydantic
    bodies for the editing endpoints.
  - ``_validate_eco`` / ``_validate_text_field`` — input guards for
    every endpoint that takes an eco code or a name/line string.
  - ``_MASTERY_EMA_STEP`` — exponential-moving-average step constant
    used by the drill-result endpoint.
"""

from __future__ import annotations

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it.  Pylint
# flags every such parameter as unused; disabling the rule file-wide
# rather than per-handler keeps the diff stable as new endpoints land.
# pylint: disable=unused-argument

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from llm.seca.auth.router import get_current_player
from llm.seca.shared_limiter import limiter
from llm.seca.storage.repo import (
    delete_opening,
    list_repertoire,
    seed_default_repertoire,
    set_active_opening,
    update_opening_mastery,
    upsert_opening,
)

router = APIRouter()


# Mirrors OpeningsActivity.DEFAULT_REPERTOIRE 1-for-1 (drift here would
# produce a different list on first launch than the user sees after
# their first edit).  Kept as the single source of truth on the server
# side; the Android side is verified against this by
# ``test_repertoire_endpoint.py``.
DEFAULT_REPERTOIRE: list[dict] = [
    {
        "eco": "C84",
        "name": "Ruy Lopez · Closed",
        "line": "1.e4 e5 2.♘f3 ♘c6 3.♗b5 a6",
        "mastery": 0.78,
        "is_active": True,
        "ordinal": 0,
    },
    {
        "eco": "B22",
        "name": "Sicilian · Alapin",
        "line": "1.e4 c5 2.c3 ♘f6 3.e5 ♘d5",
        "mastery": 0.55,
        "is_active": False,
        "ordinal": 1,
    },
    {
        "eco": "D02",
        "name": "Queen's Pawn · London",
        "line": "1.d4 d5 2.♘f3 ♘f6 3.♗f4",
        "mastery": 0.42,
        "is_active": False,
        "ordinal": 2,
    },
    {
        "eco": "A04",
        "name": "Réti opening",
        "line": "1.♘f3 d5 2.c4 e6 3.g3",
        "mastery": 0.18,
        "is_active": False,
        "ordinal": 3,
    },
]


_ECO_RE = re.compile(r"^[A-E][0-9]{2}$|^[A-Z][0-9A-Z]{1,7}$")


def _validate_eco(eco: str) -> str:
    """Permissive ECO validation: standard A00–E99 codes plus
    user-coined identifiers up to 8 chars.  Strips whitespace."""
    eco = eco.strip()
    if not eco:
        raise HTTPException(status_code=400, detail="eco must not be empty")
    if len(eco) > 8:
        raise HTTPException(status_code=400, detail="eco too long (max 8 chars)")
    for ch in eco:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise HTTPException(status_code=400, detail="eco contains control characters")
    if not _ECO_RE.match(eco):
        raise HTTPException(
            status_code=400,
            detail="eco must match standard A00–E99 or be a 1–8 char alnum identifier",
        )
    return eco


def _validate_text_field(value: str, field: str, max_len: int) -> str:
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{field} must not be empty")
    if len(value) > max_len:
        raise HTTPException(status_code=400, detail=f"{field} too long (max {max_len} chars)")
    for ch in value:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise HTTPException(status_code=400, detail=f"{field} contains control characters")
    return value


class RepertoireEntryRequest(BaseModel):
    """Body of POST /repertoire — add or update one opening.

    `mastery` and `is_active` are optional; new entries default to
    mastery=0 and is_active=false.  Use POST /repertoire/{eco}/active
    to change the active line (it enforces the one-active invariant
    across the player's whole list).
    """

    eco: str
    name: str
    line: str
    mastery: float = 0.0

    @field_validator("mastery")
    @classmethod
    def validate_mastery(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("mastery must be in [0.0, 1.0]")
        return float(v)


class DrillResultRequest(BaseModel):
    """Body of POST /repertoire/{eco}/drill-result.

    `outcome` is a self-rated [0, 1] score from the user's drill
    session — the Android client maps "Nailed it" / "Mostly" /
    "Forgot it" to 1.0 / 0.6 / 0.2 respectively.  Future revisions
    may compute it from a real per-move drill engine.
    """

    outcome: float

    @field_validator("outcome")
    @classmethod
    def validate_outcome(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("outcome must be in [0.0, 1.0]")
        return float(v)


# Exponential-moving-average step for mastery updates: a single drill
# nudges mastery toward the outcome by this fraction.  0.2 means
# 5 perfect drills move a fresh line from 0 → ~0.67, and one bad
# drill of a well-mastered line never collapses it below ~80% of the
# previous value — keeps the mastery bar feeling earned, not
# whiplashed.
_MASTERY_EMA_STEP = 0.2


@router.get("/repertoire")
@limiter.limit("30/minute")
def get_repertoire(request: Request, player=Depends(get_current_player)):
    """Return the authenticated player's opening repertoire.

    Behaviour:
      - When the player has saved entries, returns those (ordered by
        ordinal ASC, id ASC for stable display).
      - When the player has no saved entries, returns DEFAULT_REPERTOIRE
        — a fresh user sees the canonical 4 lines without the screen
        reading empty.  The defaults are NOT inserted on read to keep
        the GET endpoint side-effect-free; the editing endpoints below
        materialise them on first write via seed_default_repertoire.

    Response shape: {"openings": [...]} where each opening is
    {eco, name, line, mastery, is_active, ordinal}.
    """
    saved = list_repertoire(str(player.id))
    return {"openings": saved if saved else DEFAULT_REPERTOIRE}


# ---------------------------------------------------------------------------
# Repertoire editing — POST add / DELETE remove / POST set-active
# ---------------------------------------------------------------------------
#
# All three endpoints first call seed_default_repertoire() so the
# user can edit the defaults they see in the GET response without an
# explicit "save defaults" step.  Without this, a fresh user who taps
# "set as active" on a default line would silently fail because no
# row exists for that eco yet.


@router.post("/repertoire")
@limiter.limit("30/minute")
def add_or_update_opening(
    req: RepertoireEntryRequest,
    request: Request,
    player=Depends(get_current_player),
):
    """Add a new opening or update an existing one (upsert by eco).

    The user-facing flow: tap "+" in OpeningsActivity → fill in
    ECO/name/line → POST.  The server seeds defaults first if the
    player has no entries yet, then upserts the new line.

    Returns the full updated repertoire so the client can re-render
    in one round-trip.
    """
    seed_default_repertoire(str(player.id), DEFAULT_REPERTOIRE)
    eco = _validate_eco(req.eco)
    name = _validate_text_field(req.name, "name", 200)
    line = _validate_text_field(req.line, "line", 500)
    upsert_opening(
        player_id=str(player.id),
        eco=eco,
        name=name,
        line=line,
        mastery=req.mastery,
    )
    return {"openings": list_repertoire(str(player.id))}


@router.delete("/repertoire/{eco}")
@limiter.limit("30/minute")
def delete_opening_endpoint(
    eco: str,
    request: Request,
    player=Depends(get_current_player),
):
    """Remove an opening from the player's repertoire.

    Validates the path eco the same way POST /repertoire validates
    its body eco — same character set, same length cap, same
    rejection of control chars.

    Seeds defaults first so a user who deletes one of the canonical
    lines actually drops it from their persistent list (vs. the
    library-of-defaults reappearing on the next GET).

    Returns 404 when no row matches; the client treats that as
    "already gone" and refreshes the list either way.
    """
    eco = _validate_eco(eco)
    seed_default_repertoire(str(player.id), DEFAULT_REPERTOIRE)
    if not delete_opening(str(player.id), eco):
        raise HTTPException(status_code=404, detail="opening not found")
    return {"openings": list_repertoire(str(player.id))}


@router.post("/repertoire/{eco}/drill-result")
@limiter.limit("30/minute")
def drill_result_endpoint(
    eco: str,
    req: DrillResultRequest,
    request: Request,
    player=Depends(get_current_player),
):
    """Apply one drill outcome to the named opening's mastery.

    Update math (EMA):
        new = old + step * (outcome - old)
    where step=_MASTERY_EMA_STEP.  Clamped to [0, 1] defensively,
    though both inputs are bounded so the update can't escape the
    range without floating-point noise.

    Seeds defaults first so a user who drills one of the canonical
    lines materialises their personal copy before the mastery bump
    lands.

    Returns the full updated repertoire so the client re-renders
    in one round-trip.
    """
    eco = _validate_eco(eco)
    seed_default_repertoire(str(player.id), DEFAULT_REPERTOIRE)
    saved = list_repertoire(str(player.id))
    target = next((o for o in saved if o["eco"] == eco), None)
    if target is None:
        raise HTTPException(status_code=404, detail="opening not found")

    old = float(target["mastery"])
    proposed = old + _MASTERY_EMA_STEP * (req.outcome - old)
    new_mastery = max(0.0, min(1.0, proposed))

    if not update_opening_mastery(str(player.id), eco, new_mastery):
        # Race: row vanished between our seed/list and the update —
        # treat as 404 so the client can refresh and retry.
        raise HTTPException(status_code=404, detail="opening not found")

    return {"openings": list_repertoire(str(player.id))}


@router.post("/repertoire/{eco}/active")
@limiter.limit("30/minute")
def set_active_opening_endpoint(
    eco: str,
    request: Request,
    player=Depends(get_current_player),
):
    """Mark [eco] as the player's active line.  The two-write
    transaction in repo.set_active_opening enforces the
    "exactly one active" invariant atomically.

    Seeds defaults first so a user who taps "set active" on a
    default line gets it materialised + promoted in one call.

    Returns 404 when the eco doesn't exist for this player after
    seeding (i.e. it's neither a default nor something they've
    added).
    """
    eco = _validate_eco(eco)
    seed_default_repertoire(str(player.id), DEFAULT_REPERTOIRE)
    if not set_active_opening(str(player.id), eco):
        raise HTTPException(status_code=404, detail="opening not found")
    return {"openings": list_repertoire(str(player.id))}
