"""HTTP surface for the standalone puzzle trainer.

One endpoint, authenticated:

* ``GET /puzzles/next`` — serve one practice puzzle for the Puzzles
  tab: live-fetched from Lichess's public puzzle database when
  reachable, otherwise drawn from the curated local corpus the study
  plan already ships.

Where the puzzle comes from
---------------------------
Primary source is Lichess ``GET /api/puzzle/next`` via the existing
allowlisted client wrapper (``llm.seca.lichess.client
.fetch_puzzle_by_theme``) with the ``"mix"`` angle (Lichess's "healthy
mix") and a difficulty band derived from the authenticated player's
rating.  Every failure mode — kill-switch off, rate limit, upstream
5xx, malformed body, illegal derived position — falls back to a random
skill-banded pick from the curated YAML corpus
(``llm/seca/coach/study_plan/library``), so the tab keeps working with
zero Lichess availability.

Trust boundary
--------------
The puzzle is a training POSITION only.  Its ``expected_move_uci`` is
Lichess's (or the corpus author's) solution move, stored as a display /
short-circuit hint; whether an attempt counts as solved is decided by
the LOCAL engine on ``POST /training/verify-replay``, and XP flows
through ``POST /training/solve`` (``source_type="standard_puzzle"``,
``source_ref=<puzzle_id>``) with per-puzzle dedup.  Lichess evaluations
are never requested or used (``docs/ARCHITECTURE.md``).
"""

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it.  Pylint
# flags every such parameter as unused; disabling the rule file-wide
# rather than per-handler keeps the diff stable as new endpoints land.
# pylint: disable=unused-argument

from __future__ import annotations

import logging
import os
import random

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from llm.seca.auth.models import Player
from llm.seca.auth.router import get_current_player
from llm.seca.coach.study_plan.library import LibraryPuzzle, load_library
from llm.seca.coach.study_plan.verdict import skill_hint_for_rating
from llm.seca.shared_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/puzzles", tags=["puzzles"])


# Lichess angle served by the trainer.  "mix" is Lichess's default
# healthy-mix angle (un-themed practice); themed drilling stays the
# study plan's job.  Must be in the client's ``_PUZZLE_ANGLE_ALLOWED``
# allowlist (pinned by ``llm/tests/test_puzzles_next.py``).
PUZZLE_ANGLE: str = "mix"


# Player skill band (``skill_hint_for_rating``) -> Lichess difficulty
# band for /api/puzzle/next.  Values must be in the client's
# ``_PUZZLE_DIFFICULTY_ALLOWED`` allowlist (pinned by the same test).
SKILL_TO_DIFFICULTY: dict[str, str] = {
    "beginner": "easier",
    "intermediate": "normal",
    "advanced": "harder",
}


def _lichess_enabled() -> bool:
    # Kill-switch: ``PUZZLES_LICHESS_ENABLED=0`` disables the live path
    # (corpus only) without a redeploy — same posture as the study
    # plan's ``STUDY_PLAN_LICHESS_ENABLED``.
    return os.getenv("PUZZLES_LICHESS_ENABLED", "1") == "1"


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class PuzzleNextResponse(BaseModel):
    """One practice puzzle for the trainer sheet.

    ``puzzle_id`` is the stable identifier the client passes back to
    ``POST /training/solve`` as ``source_ref`` so each puzzle is
    credit-once.  Lichess ids are namespaced (``lichess_<id>``) so they
    can never collide with a corpus YAML id.
    """

    puzzle_id: str
    fen: str
    expected_move_uci: str

    theme: str
    """Corpus theme tag for library picks (``THEME_VOCABULARY``);
    ``"mix"`` for Lichess picks (the trainer serves un-themed practice)."""

    difficulty: str
    """``beginner`` / ``intermediate`` / ``advanced`` — corpus band for
    library picks; derived from the Lichess puzzle rating otherwise."""

    source: str
    """``"lichess"`` (live fetch) or ``"library"`` (curated corpus)."""

    rating: int | None = None
    """Lichess puzzle rating when known; ``null`` for corpus picks."""


# ---------------------------------------------------------------------------
# Corpus fallback
# ---------------------------------------------------------------------------

# Lazy module-level cache: the YAML corpus is immutable per process, so
# one load serves every request.  Loaded on first fallback (not at
# import) so server boot doesn't pay for it and a corpus problem
# surfaces as a request-time 500, matching the study-plan agent's
# lazy-load posture.
_library_cache: dict[str, list[LibraryPuzzle]] | None = None


def _library() -> dict[str, list[LibraryPuzzle]]:
    global _library_cache  # pylint: disable=global-statement
    if _library_cache is None:
        _library_cache = load_library()
    return _library_cache


def _corpus_pick(skill_hint: str) -> LibraryPuzzle | None:
    """Random corpus puzzle, biased to the player's difficulty band.

    Exact-band picks when the band has entries; the whole corpus
    otherwise (a beginner drawing an advanced puzzle beats no puzzle).
    Non-deterministic by design — the trainer is an endless stream, so
    repeat-avoidance is the client's concern (it just fetches again).
    Returns ``None`` only when the corpus is empty.
    """
    pool = [p for bucket in _library().values() for p in bucket]
    if not pool:
        return None
    banded = [p for p in pool if p.difficulty == skill_hint]
    return random.choice(banded if banded else pool)


# ---------------------------------------------------------------------------
# GET /puzzles/next
# ---------------------------------------------------------------------------


@router.get("/next", response_model=PuzzleNextResponse)
@limiter.limit("30/minute")
def next_puzzle(
    request: Request,
    player: Player = Depends(get_current_player),
) -> PuzzleNextResponse:
    """Serve one practice puzzle (Lichess first, corpus fallback).

    The rate limit is deliberately tighter than the other /training
    surfaces (30/min vs 60/min): each call can hit Lichess upstream,
    and a human solving puzzles cannot plausibly need more than one
    fetch every two seconds.

    Errors
    ------
    * ``503`` — Lichess unavailable AND the local corpus is empty
      (misbuilt image); the client shows a soft retry message.
    """
    skill_hint = skill_hint_for_rating(float(player.rating or 1200.0))

    if _lichess_enabled():
        # Lazy import so the network client isn't pulled into the
        # server-boot import graph — same posture as the study plan's
        # lichess_puzzles module.
        from llm.seca.lichess.client import (  # noqa: PLC0415
            LichessClientError,
            fetch_puzzle_by_theme,
        )

        try:
            puzzle = fetch_puzzle_by_theme(
                PUZZLE_ANGLE,
                difficulty=SKILL_TO_DIFFICULTY.get(skill_hint, "normal"),
            )
            return PuzzleNextResponse(
                puzzle_id=f"lichess_{puzzle.id}",
                fen=puzzle.solver_fen,
                expected_move_uci=puzzle.solver_move_uci,
                theme="mix",
                difficulty=(
                    skill_hint_for_rating(float(puzzle.rating)) if puzzle.rating else skill_hint
                ),
                source="lichess",
                rating=puzzle.rating or None,
            )
        except LichessClientError as exc:
            # Expected upstream failure modes (rate limit, 5xx, parse,
            # illegal derived position) — quiet fallback, INFO for the
            # ops trail.
            logger.info("puzzles/next lichess fetch failed (%s); serving corpus", exc)
        except Exception:  # noqa: BLE001 — fallback exists; don't 500 the tab
            logger.exception("puzzles/next unexpected lichess error; serving corpus")

    pick = _corpus_pick(skill_hint)
    if pick is None:
        raise HTTPException(status_code=503, detail="no puzzle available")
    return PuzzleNextResponse(
        puzzle_id=pick.id,
        fen=pick.fen,
        expected_move_uci=pick.expected_move_uci,
        theme=pick.theme,
        difficulty=pick.difficulty,
        source="library",
        rating=None,
    )
