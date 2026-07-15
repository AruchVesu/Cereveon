"""Live Lichess puzzle source for per-mistake study-plan practice slots.

The study-plan agent fills a mistake's day-3 / day-7 slots with practice
puzzles that should train the day-0 mistake's LESSON — same theme, the same
SIDE the player was on when they erred ("the weakest player's side"), and,
crucially, the same ROLE.  The curated local corpus
(``library/puzzles.yaml``) is a tiny seed, so on its own it can't offer much
variety or a reliable side match.  This module closes that gap by pulling
ready-made puzzles from Lichess's puzzle database at plan-generation time.

Role-aware theme mapping
------------------------
Lichess theme puzzles are always solved from the WINNING side: a
``hangingPiece`` puzzle has the solver CAPTURE the loose piece, an
``exposedKing`` puzzle has the solver ATTACK the bare king.  That is the
right seat for exploit-themes (the mistake was MISSING a fork/pin — so drill
finding forks/pins), but exactly the wrong seat for defence-themes: a player
whose queen got harassed off the board would be served "win the opponent's
early queen" drills — attacking the weakness they SUFFER from instead of
practicing keeping their own pieces safe (the launch-feedback bug).  Themes
whose lesson is protecting your OWN material/king therefore map to Lichess's
``defensiveMove`` angle, where the solver must find the move that keeps
their own position safe — the actual skill the mistake showed was missing.

Depth and difficulty
--------------------
A single-move drill is a much weaker exercise than the source puzzle (the
first move of a combination is trivial out of context), so the fetcher keeps
the FULL Lichess solution line (``LichessPuzzle.solution_line_uci``) and
prefers puzzles with at least two solver moves.  Difficulty bands fan out
from the player's own band UPWARD first (the "too easy" half of the same
launch feedback), and the day-3 / day-7 pair is ordered by puzzle rating so
the week gets harder as it progresses.

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
The fetched puzzle is a training POSITION only.  Its solution moves are
stored as display / walk-through hints; whether a replay counts as solved is
decided by the LOCAL engine on ``POST /training/verify-replay`` — for every
step of a multi-move line, not just the first.  Lichess evaluations are
never requested or used.  See ``llm/seca/lichess/client.py`` >
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
# puzzle "angle" theme slugs accepted by /api/puzzle/next, ROLE-AWARE (see
# the module docstring):
#
# * Exploit-role themes — the mistake was MISSING a tactic; the drill is to
#   find that tactic, so the theme's own slug is the right seat.
# * Defend-role themes — the mistake was endangering the player's OWN
#   queen / piece / king / back rank; the matching theme slugs would put the
#   player in the attacker's seat (Lichess theme puzzles are solved from the
#   winning side), so these map to ``defensiveMove``: find the move that
#   keeps your own position safe.
# * Phase themes map to the matching phase angle.
#
# ``tempo`` / ``generic`` have no faithful Lichess counterpart and fall
# through to the local corpus.  Every VALUE here must also be in the
# client's ``_PUZZLE_ANGLE_ALLOWED`` allowlist (pinned by a test).
_THEME_TO_ANGLE: dict[str, str] = {
    # Exploit-role: drill the tactic the player missed.
    "fork": "fork",
    "pin": "pin",
    # Defend-role: drill keeping your own material / king safe.
    "queen_safety": "defensiveMove",
    "hung_piece": "defensiveMove",
    "king_safety": "defensiveMove",
    "back_rank": "defensiveMove",
    # Phase skills.
    "opening_principles": "opening",
    "endgame_technique": "endgame",
}


# Lichess difficulty bands, easiest -> hardest.  We walk from the player's
# skill-appropriate band UPWARD before reaching down (see _difficulty_order).
_DIFFICULTY_BANDS: tuple[str, ...] = ("easiest", "easier", "normal", "harder", "hardest")

_SKILL_TO_DIFFICULTY: dict[str, str] = {
    "beginner": "easier",
    "intermediate": "normal",
    "advanced": "harder",
}


# A puzzle is worth walking as a multi-move drill when its solution carries
# at least two solver decisions: solver, opponent reply, solver = 3 plies.
# (Lichess lines always end on a solver move.)
_MIN_MULTI_MOVE_PLIES: int = 3


def _difficulty_order(skill_hint: str) -> list[str]:
    """Difficulty bands to try: the player's own band first, then every band
    ABOVE it (nearest first), then the bands below (nearest first).

    "The tasks are too easy" was direct launch feedback, so when the
    player's own band can't fill the pair the fetch stretches UP before it
    reaches down — an intermediate player probes ``normal -> harder ->
    hardest -> easier -> easiest``.  The list is a permutation of
    ``_DIFFICULTY_BANDS`` (all five, no repeats); the fetch loop cycles it
    when the budget allows more calls than there are bands.
    """
    primary = _SKILL_TO_DIFFICULTY.get(skill_hint, "normal")
    idx = _DIFFICULTY_BANDS.index(primary)
    harder = list(_DIFFICULTY_BANDS[idx + 1 :])
    easier = list(reversed(_DIFFICULTY_BANDS[:idx]))
    return [primary, *harder, *easier]


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


def _is_multi_move(puzzle: LichessPuzzle) -> bool:
    """True when the puzzle carries at least two solver decisions."""
    return len(puzzle.solution_line_uci) >= _MIN_MULTI_MOVE_PLIES


def _to_library_puzzle(puzzle: LichessPuzzle, theme: str) -> LibraryPuzzle:
    """Adapt a client ``LichessPuzzle`` to the ``LibraryPuzzle`` the agent
    already knows how to write.  ``theme`` is OUR theme (the plan's), not the
    Lichess tag set — the agent reads ``fen`` + ``expected_move_uci`` +
    ``solution_line_uci`` downstream, but we fill every field so the object
    is self-describing in logs.  ``id`` is namespaced so it can never collide
    with a YAML id."""
    return LibraryPuzzle(
        id=f"lichess_{puzzle.id}",
        theme=theme,
        difficulty=skill_hint_for_rating(float(puzzle.rating)),
        fen=puzzle.solver_fen,
        expected_move_uci=puzzle.solver_move_uci,
        description=f"Lichess puzzle {puzzle.id} (themes: {', '.join(puzzle.themes[:6])})",
        solution_line_uci=puzzle.solution_line_uci,
    )


def fetch_side_matched_variants(
    *,
    theme: str,
    side_to_move: chess.Color,
    skill_hint: str,
) -> list[LibraryPuzzle]:
    """Collect up to two theme-, role- and side-matched Lichess puzzles.

    Parameters
    ----------
    theme:
        The plan's day-0 mistake theme (one of ``THEME_VOCABULARY``).  Mapped
        to a Lichess angle slug via the role-aware ``_THEME_TO_ANGLE``; an
        unmapped theme returns ``[]`` immediately (caller uses the local
        corpus).
    side_to_move:
        The side to move in the day-0 mistake position (the player's side).
        Only puzzles whose solver is on this side are kept.
    skill_hint:
        ``beginner`` / ``intermediate`` / ``advanced`` from
        ``skill_hint_for_rating`` — picks which difficulty band is tried
        first (the fan then stretches harder before easier).

    Selection
    ---------
    The loop keeps fetching (one call per difficulty band, cycling) until it
    has TWO side-matched multi-move puzzles or the budget runs out; two
    single-move side matches alone don't stop the hunt.  The final pair
    prefers multi-move puzzles and is ordered by Lichess rating ascending so
    day 3 gets the easier puzzle and day 7 the harder one.

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

    bands = _difficulty_order(skill_hint)
    matched: list[LichessPuzzle] = []
    seen_ids: set[str] = set()

    for call_index in range(_max_fetches()):
        if sum(1 for p in matched if _is_multi_move(p)) >= 2:
            break
        difficulty = bands[call_index % len(bands)]
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
        matched.append(puzzle)

    # Multi-move puzzles first (a one-move drill is the degraded case), then
    # order the surviving pair by rating so the week ramps up: day 3 easier,
    # day 7 harder.
    multi = [p for p in matched if _is_multi_move(p)]
    single = [p for p in matched if not _is_multi_move(p)]
    pair = (multi + single)[:2]
    pair.sort(key=lambda p: p.rating)
    return [_to_library_puzzle(p, theme) for p in pair]
