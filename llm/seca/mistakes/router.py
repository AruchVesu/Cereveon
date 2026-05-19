"""HTTP surface for mistake-replay verification.

One endpoint, authenticated:

* ``POST /training/verify-replay`` — verify a single mistake-replay
  attempt against the engine.  Returns whether the user's move is
  within 30 cp of the engine's best, plus the engine's preferred move
  for optional UI display.

Why this lives in ``llm.seca.mistakes`` rather than alongside the
existing /training/solve handler in ``llm.seca.training.router``:
mistake-replay is one source type among several (weekly_microtask,
standard_puzzle, ...).  Keeping the source-specific verification
logic in a per-source package leaves /training/solve generic — it
just records a verified solve, regardless of how the verification
happened upstream.  Other sources will own their own verifier
packages (weekly digest, puzzle DB, ...) but write to the same
/training/solve sink.

Shares the ``/training`` URL prefix with the solve router because
both are "training-related actions" from the client's POV; FastAPI
handles multiple routers with the same prefix transparently.
"""

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it.  Pylint
# flags every such parameter as unused; disabling the rule file-wide
# rather than per-handler keeps the diff stable as new endpoints land.
# pylint: disable=unused-argument

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from llm.seca.auth.models import Player
from llm.seca.auth.router import get_current_player
from llm.seca.mistakes.verify import (
    EngineUnavailable,
    VerifyError,
    VerifyResult,
    verify_replay_move,
)
from llm.seca.shared_limiter import limiter

if TYPE_CHECKING:
    from llm.seca.engines.stockfish.pool import StockfishEnginePool

logger = logging.getLogger(__name__)


# Hard upper bounds on caller-supplied strings.  FEN is at most 90-ish
# chars in practice; we cap at 200 to give padding without permitting
# pathological input.  UCI move is at most 5 chars (e.g. ``e7e8q``);
# we cap at 8 for safety.
_MAX_FEN_LEN = 200
_MAX_UCI_LEN = 8


# ---------------------------------------------------------------------------
# Engine-pool injection
# ---------------------------------------------------------------------------
#
# The Stockfish pool is owned by ``llm/server.py`` (created in the
# lifespan handler).  This router needs read access to it without
# importing ``llm.server`` (that would create a circular import on
# server.py's side, since server.py registers this router).  Pattern:
# ``llm.server`` calls ``set_engine_pool()`` at lifespan-startup and
# the handler reads from a module-global.  Tests can set a fake pool
# the same way.


_engine_pool: "StockfishEnginePool | None" = None


def set_engine_pool(pool: "StockfishEnginePool | None") -> None:
    """Inject the Stockfish pool reference at server startup.

    Called from ``llm/server.py``'s lifespan handler after the pool is
    constructed; called again with ``None`` on shutdown so the
    reference doesn't outlive the pool itself.
    """
    # The module-global is the documented injection point for the
    # Stockfish pool reference; see the module docstring above for
    # the rationale (avoids the circular import that
    # ``from llm.server import engine_pool`` would create).
    global _engine_pool  # pylint: disable=global-statement
    _engine_pool = pool


def _require_pool() -> "StockfishEnginePool":
    pool = _engine_pool
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail="engine pool unavailable; cannot verify replay",
        )
    return pool


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class VerifyReplayRequest(BaseModel):
    fen: str
    move_uci: str

    @field_validator("fen")
    @classmethod
    def _validate_fen(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("fen must not be empty")
        if len(v) > _MAX_FEN_LEN:
            raise ValueError(f"fen must be at most {_MAX_FEN_LEN} chars")
        return v

    @field_validator("move_uci")
    @classmethod
    def _validate_move(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("move_uci must not be empty")
        if len(v) > _MAX_UCI_LEN:
            raise ValueError(f"move_uci must be at most {_MAX_UCI_LEN} chars")
        return v


class VerifyReplayResponse(BaseModel):
    is_correct: bool
    engine_best_uci: str
    eval_loss_cp: int


# ---------------------------------------------------------------------------
# POST /training/verify-replay
# ---------------------------------------------------------------------------


router = APIRouter(prefix="/training", tags=["training"])


@router.post("/verify-replay", response_model=VerifyReplayResponse)
@limiter.limit("60/minute")
def verify_replay(
    req: VerifyReplayRequest,
    request: Request,
    _player: Player = Depends(get_current_player),
) -> VerifyReplayResponse:
    """Verify a single mistake-replay attempt against the engine.

    The endpoint is the trust anchor for the Phase 3 XP-credit path:
    the Android replay sheet calls this BEFORE calling
    ``POST /training/solve``, so an unverified move never moves the
    counter.

    Returns ``is_correct=True`` when the user's move gives up at most
    ``VERIFY_THRESHOLD_CP`` centipawns vs the engine's best move.
    Returns ``is_correct=False`` on a genuine "engine ran and the
    answer is no" — that's NOT an error, the replay UI shows
    "Not quite, try again" and the user moves on.

    Errors:
        400 — request validation failed (bad FEN, illegal move, etc.)
        503 — engine pool unavailable (boot-time failure or queue
              timeout).  Distinct from 500 so the client can show a
              soft "engine is busy" message and offer a retry.
    """
    pool = _require_pool()

    try:
        result: VerifyResult = verify_replay_move(req.fen, req.move_uci, pool)
    except VerifyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EngineUnavailable as exc:
        logger.warning("verify-replay: engine unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="engine pool unavailable; cannot verify replay",
        ) from exc

    return VerifyReplayResponse(
        is_correct=result.is_correct,
        engine_best_uci=result.engine_best_uci,
        eval_loss_cp=result.eval_loss_cp,
    )
