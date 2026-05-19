"""HTTP surface for training-completion events.

One endpoint, authenticated:

* ``POST /training/solve`` — credit the authenticated player with one
  verified-solve event.

The endpoint trusts the caller's claim that a solve happened — engine
verification is the *caller*'s responsibility (Phase 3 will run the
move-vs-engine-best check on the client + a server-side double-check
before posting here).  This module's job is the persistence + counter
update, plus idempotency by ``(player_id, source_type, source_ref)``
so a flaky network retry can't double-credit XP.

Phase 2 ships dormant — no caller yet.  The endpoint exists so Phase 3
can wire the mistake-replay UI without also having to introduce the
backend in the same change.
"""

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it.  Pylint
# flags every such parameter as unused; disabling the rule file-wide
# rather than per-handler keeps the diff stable as new endpoints land.
# pylint: disable=unused-argument

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.models import Player
from llm.seca.auth.router import get_current_player, get_db
from llm.seca.shared_limiter import limiter
from llm.seca.training.models import (
    SOURCE_TYPES,
    XP_PER_SOLVE,
    TrainingCompletion,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/training", tags=["training"])


# Conservative upper bound on the caller-supplied opaque identifier.
# 200 chars is enough for "game_<uuid>:move_<n>", a Lichess puzzle id
# with prefix, or a weekly-digest row id; tighter than the DB column's
# implicit limit so we never write a 10 KB blob from a malformed
# client.
_MAX_SOURCE_REF_LEN = 200


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SolveRequest(BaseModel):
    source_type: str
    # Optional — see ``TrainingCompletion.source_ref`` docstring for
    # when callers may legitimately omit this (catch-all completions).
    source_ref: str | None = None

    @field_validator("source_type")
    @classmethod
    def _validate_source_type(cls, v: str) -> str:
        if v not in SOURCE_TYPES:
            allowed = ", ".join(sorted(SOURCE_TYPES))
            raise ValueError(f"source_type must be one of: {allowed}")
        return v

    @field_validator("source_ref")
    @classmethod
    def _validate_source_ref(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            # An all-whitespace string is indistinguishable from "no
            # ref" at the storage layer; normalise to NULL so the
            # idempotency guard's NULL-distinct semantics kick in.
            return None
        if len(v) > _MAX_SOURCE_REF_LEN:
            raise ValueError(f"source_ref must be at most {_MAX_SOURCE_REF_LEN} chars")
        return v


class SolveResponse(BaseModel):
    # XP awarded for THIS request — fixed at XP_PER_SOLVE for a new
    # row, zero when the call deduped against an existing completion.
    # Lets the client tell the difference between "you earned XP" and
    # "we already had this one" without rendering two response shapes.
    xp_awarded: int

    # New running total on the player row after this call.  Lets the
    # client update its PREF_TRAINING_XP cache + Home Level/XP kicker
    # without a separate /auth/me round trip.
    training_xp: int

    # ISO-8601 timestamp.  For a brand-new completion this is the row
    # that was just inserted; for a dedup hit this is the historical
    # row's timestamp (so the client can present an honest "solved on
    # <date>" if it ever shows completion history).
    completed_at: str


# ---------------------------------------------------------------------------
# POST /training/solve
# ---------------------------------------------------------------------------


@router.post("/solve", response_model=SolveResponse)
@limiter.limit("60/minute")
def solve(
    req: SolveRequest,
    request: Request,
    player: Player = Depends(get_current_player),
    db: DBSession = Depends(get_db),
) -> SolveResponse:
    """Credit one verified-solve event to the authenticated player.

    Idempotent on ``(player_id, source_type, source_ref)`` when
    ``source_ref`` is not null: a retry of the same logical solve
    returns the original row's data with ``xp_awarded=0`` so the
    counter doesn't double-bump.  Rows with ``source_ref=None`` are
    NOT deduped (see the module docstring + model comment) — the
    intent is that the caller always supplies a stable ref for solves
    where dedup matters.
    """
    # Dedup pre-check: only meaningful when source_ref is non-null,
    # since the unique constraint allows NULL duplicates.  We pre-
    # check (instead of relying solely on the IntegrityError path)
    # so the response can carry the original completion's
    # ``completed_at`` without an extra round-trip.
    if req.source_ref is not None:
        existing = (
            db.query(TrainingCompletion)
            .filter(
                TrainingCompletion.player_id == player.id,
                TrainingCompletion.source_type == req.source_type,
                TrainingCompletion.source_ref == req.source_ref,
            )
            .first()
        )
        if existing is not None:
            return SolveResponse(
                xp_awarded=0,
                training_xp=int(player.training_xp or 0),
                completed_at=existing.completed_at.isoformat(),
            )

    completion = TrainingCompletion(
        player_id=player.id,
        source_type=req.source_type,
        source_ref=req.source_ref,
        xp_awarded=XP_PER_SOLVE,
    )
    db.add(completion)
    player.training_xp = int(player.training_xp or 0) + XP_PER_SOLVE
    try:
        db.commit()
    except IntegrityError:
        # Race: two concurrent requests with the same (player,
        # source_type, source_ref) both passed the pre-check, one
        # committed first, the second hit the unique index.  Roll
        # back, re-fetch the existing row, and return it — same
        # observable behaviour as the pre-check dedup path.  We do
        # NOT raise here: the caller's intent (record a solve) is
        # already satisfied by the first commit.
        db.rollback()
        # Re-read the player so the in-memory training_xp matches
        # the committed value (it would otherwise still carry the
        # rolled-back +10 from this transaction).
        db.refresh(player)
        existing = (
            db.query(TrainingCompletion)
            .filter(
                TrainingCompletion.player_id == player.id,
                TrainingCompletion.source_type == req.source_type,
                TrainingCompletion.source_ref == req.source_ref,
            )
            .first()
        )
        if existing is None:
            # Should be unreachable — the IntegrityError implies a
            # row exists.  Surface as a 500 if it does happen; we
            # prefer the loud signal over silently double-crediting.
            logger.error(
                "IntegrityError on /training/solve but no row found for "
                "player_id=%s source_type=%s source_ref=%s",
                player.id,
                req.source_type,
                req.source_ref,
            )
            raise HTTPException(status_code=500, detail="Conflict on training solve")
        return SolveResponse(
            xp_awarded=0,
            training_xp=int(player.training_xp or 0),
            completed_at=existing.completed_at.isoformat(),
        )

    db.refresh(completion)
    return SolveResponse(
        xp_awarded=XP_PER_SOLVE,
        training_xp=int(player.training_xp or 0),
        completed_at=completion.completed_at.isoformat(),
    )
