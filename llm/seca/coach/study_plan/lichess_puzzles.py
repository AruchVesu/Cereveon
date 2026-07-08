"""Live Lichess puzzle source for per-mistake study-plan practice slots.

The study-plan agent fills a mistake's day-3 / day-7 slots with practice
puzzles that should resemble the day-0 mistake — same tactical THEME and,
crucially, the same SIDE the player was on when they erred ("the weakest
player's side").  The curated local corpus (``library/puzzles.yaml``) is a
tiny, entirely White-to-move seed, so on its own it can't match a Black-side
mistake.  This module closes that gap by pulling ready-made, theme-matched,
correct-side puzzles from Lichess's puzzle database at plan-generation time.

Where this sits in the pipeline
-------------------------------
``agent._populate_library_variants`` calls ``fetch_side_matched_variants``
FIRST.  When it returns two puzzles they become day 3 / day 7; otherwise the
agent falls back to the existing local-corpus selection
(``library.pick_two_puzzles_theme_first``).  Every failure mode here —
feature flag off, no Lichess slug for the theme, network / rate-limit error,
fewer than two side-matched puzzles found — returns ``[]`` so behaviour
degrades to exactly what shipped before this module existed.

Best-effort contract
--------------------
This runs inside the ``/game/finish`` BackgroundTask, whose errors are
invisible to the user.  The function therefore NEVER raises: it swallows every
Lichess client error (and any unexpected exception) and returns whatever
side-matched puzzles it managed to collect (possibly none).

Trust boundary
--------------
The fetched puzzle is a training POSITION only.  Its solver move is stored as
a display / short-circuit hint; whether a replay counts as solved is decided
by the LOCAL engine on ``POST /training/verify-replay``.  Lichess evaluations
are never requested or used.  See ``llm/seca/lichess/client.py`` >
``fetch_puzzle_by_theme`` and ``docs/ARCHITECTURE.md``.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import chess

from llm.seca.coach.study_plan.library import LibraryPuzzle
from llm.seca.coach.study_plan.verdict import skill_hint_for_rating

if TYPE_CHECKING:  # pragma: no cover — type-only import, never executed at runtime
    from llm.seca.lichess.client import LichessPuzzle

logger = logging.getLogger(__name__)


# Our internal theme vocabulary (``verdict.THEME_VOCABULARY``) -> Lichess
# puzzle "angle" theme slugs accepted by /api/puzzle/next.  Only themes with a
# faithful Lichess counterpart are listed; ``queen_safety`` / ``tempo`` /
# ``generic`` have no clean slug, so they are intentionally absent and fall
# through to the local corpus.  Every VALUE here must also be in the client's
# ``_PUZZLE_ANGLE_ALLOWED`` allowlist (pinned by a test).
_THEME_TO_ANGLE: dict[str, str] = {
    "fork": "fork",
    "pin": "pin",
    "back_rank": "backRankMate",
    "hung_piece": "hangingPiece",
    "king_safety": "exposedKing",
    "opening_principles": "opening",
    "endgame_technique": "endgame",
}


# Lichess difficulty bands, easiest -> hardest.  We walk outward from the
# player's skill-appropriate band so the first (and thus most likely to be
# used) picks sit near the player's level, while still probing other bands to
# gather enough distinct candidates to find two on the right side.
_DIFFICULTY_BANDS: tuple[str, ...] = ("easiest", "easier", "normal", "harder", "hardest")

_SKILL_TO_DIFFICULTY: dict[str, str] = {
    "beginner": "easier",
    "intermediate": "normal",
    "advanced": "harder",
}


def _difficulty_order(skill_hint: str) -> list[str]:
    """Difficulty bands to try, nearest the player's level first.

    Starts at the skill-mapped band and fans outward, so a beginner probes
    ``easier -> easiest -> normal -> harder -> hardest`` and an advanced
    player ``harder -> normal -> hardest -> easier -> easiest``.  The list is
    a permutation of ``_DIFFICULTY_BANDS`` (all five, no repeats); the caller
    truncates it to the fetch budget.
    """
    primary = _SKILL_TO_DIFFICULTY.get(skill_hint, "normal")
    idx = _DIFFICULTY_BANDS.index(primary)
    order = [primary]
    lo, hi = idx - 1, idx + 1
    while lo >= 0 or hi < len(_DIFFICULTY_BANDS):
        if lo >= 0:
            order.append(_DIFFICULTY_BANDS[lo])
            lo -= 1
        if hi < len(_DIFFICULTY_BANDS):
            order.append(_DIFFICULTY_BANDS[hi])
            hi += 1
    return order


def _enabled() -> bool:
    # Kill-switch: ``STUDY_PLAN_LICHESS_ENABLED=0`` disables the live path
    # entirely (falls back to the local corpus) without a redeploy.
    return os.getenv("STUDY_PLAN_LICHESS_ENABLED", "1") == "1"


def _max_fetches() -> int:
    # Upper bound on /api/puzzle/next calls per plan.  One active plan per
    # player + one plan per finished game already bounds volume; this caps the
    # per-plan burst.  Clamped to [1, 10] so a bad env value can't unleash a
    # request storm or disable the path via 0.
    try:
        n = int(os.getenv("STUDY_PLAN_LICHESS_MAX_FETCHES", "5"))
    except (TypeError, ValueError):
        return 5
    return max(1, min(n, 10))


def _to_library_puzzle(puzzle: LichessPuzzle, theme: str) -> LibraryPuzzle:
    """Adapt a client ``LichessPuzzle`` to the ``LibraryPuzzle`` the agent
    already knows how to write.  ``theme`` is OUR theme (the plan's), not the
    Lichess tag set — the agent only reads ``fen`` + ``expected_move_uci``
    downstream, but we fill every field so the object is self-describing in
    logs.  ``id`` is namespaced so it can never collide with a YAML id."""
    return LibraryPuzzle(
        id=f"lichess_{puzzle.id}",
        theme=theme,
        difficulty=skill_hint_for_rating(float(puzzle.rating)),
        fen=puzzle.solver_fen,
        expected_move_uci=puzzle.solver_move_uci,
        description=f"Lichess puzzle {puzzle.id} (themes: {', '.join(puzzle.themes[:6])})",
    )


def fetch_side_matched_variants(
    *,
    theme: str,
    side_to_move: chess.Color,
    skill_hint: str,
) -> list[LibraryPuzzle]:
    """Collect up to two theme- and side-matched Lichess puzzles.

    Parameters
    ----------
    theme:
        The plan's day-0 mistake theme (one of ``THEME_VOCABULARY``).  Mapped
        to a Lichess angle slug via ``_THEME_TO_ANGLE``; an unmapped theme
        returns ``[]`` immediately (caller uses the local corpus).
    side_to_move:
        The side to move in the day-0 mistake position (the player's side).
        Only puzzles whose solver is on this side are kept.
    skill_hint:
        ``beginner`` / ``intermediate`` / ``advanced`` from
        ``skill_hint_for_rating`` — biases which difficulty band is tried
        first.

    Returns
    -------
    A list of 0, 1, or 2 ``LibraryPuzzle`` objects on the requested side, with
    distinct Lichess ids.  The agent uses the result only when it has TWO
    (otherwise it falls back), but 0/1 are returned honestly rather than
    padded.  Never raises.
    """
    if not _enabled():
        return []
    angle = _THEME_TO_ANGLE.get(theme)
    if angle is None:
        return []

    # Lazy import so the network client isn't pulled into the study-plan
    # import graph at server boot, and so a client import failure can't wedge
    # plan generation — same posture as the agent's other lazy imports.
    from llm.seca.lichess.client import (  # noqa: PLC0415
        LichessClientError,
        LichessRateLimited,
        fetch_puzzle_by_theme,
    )

    difficulties = _difficulty_order(skill_hint)[: _max_fetches()]
    picked: list[LibraryPuzzle] = []
    seen_ids: set[str] = set()

    for difficulty in difficulties:
        if len(picked) >= 2:
            break
        try:
            puzzle = fetch_puzzle_by_theme(angle, difficulty=difficulty)
        except LichessRateLimited:
            # Be a good API citizen: back off for the rest of this plan.
            logger.info("study_plan lichess fetch rate-limited; stopping early")
            break
        except LichessClientError as exc:
            # One bad response (parse error, upstream blip) shouldn't abort the
            # whole batch — try the next difficulty band.
            logger.debug("study_plan lichess fetch failed (%s); continuing", exc)
            continue
        except Exception:  # noqa: BLE001 — background task must never see this
            logger.exception("study_plan lichess fetch unexpected error; stopping")
            break

        if puzzle.side != side_to_move or puzzle.id in seen_ids:
            continue
        seen_ids.add(puzzle.id)
        picked.append(_to_library_puzzle(puzzle, theme))

    return picked
