"""Curated puzzle library for per-mistake study plans (phase 3).

Phase 1 shipped the data model with all three puzzles in a plan
pointing at the exact mistake position.  Phase 2 added an LLM-
generated coach verdict + theme classification.  Phase 3 (this
module) replaces the day-3 and day-7 puzzles with theme-matched
variants drawn from a curated YAML corpus, so a single mistake
spawns a real spaced-repetition program rather than three drills of
the same position.

The corpus lives at ``llm/seca/coach/study_plan/library/*.yaml`` — one
file per theme tag in ``THEME_VOCABULARY``.  Each entry carries the
puzzle FEN, the engine-best move (UCI), a difficulty band, and a
short description for future operator review.  The loader validates
every entry at module import: a malformed YAML (unparseable FEN,
illegal expected_move_uci, unknown theme, unknown difficulty band)
crashes the server at startup so a typo in the corpus surfaces in
``test_study_plan_library.py`` long before it ships.

Phase-3 expansion path
----------------------
The v1 seed corpus is intentionally small (~1-2 puzzles per theme).
Adding more is just a YAML edit + a test re-run — no scaffolding
changes needed.  Larger corpora (eventually 50-100 per theme) can be
sourced from public puzzle DBs (Lichess CC0, Polgar's "Chess: 5334
Problems") with care taken to engine-verify each ``expected_move_uci``
at authoring time.  Phase 3 ships the loader + selector + a starter
seed; phase 3.1 / 3.2 / ... can expand the YAML without re-litigating
the design.

Selection model
---------------
``pick_two_puzzles(library, theme, skill_hint, plan_id)`` returns two
distinct ``LibraryPuzzle`` objects (or ``None, None`` when the library
is empty for all themes):

1. **Theme filter.**  Prefer puzzles whose ``theme`` matches the
   plan's classification.  When the requested theme has no entries,
   fall back to the ``"generic"`` bucket.
2. **Skill filter.**  Bias toward puzzles whose ``difficulty`` band is
   the same as or one step adjacent to the player's
   ``skill_hint``.  Fall through to the unfiltered pool if the band
   filter would empty the candidate set.
3. **Deterministic shuffle.**  Seed ``random.Random`` from
   ``SHA256(plan_id)`` so a regenerate of the same plan picks the same
   two puzzles — the schedule on the user's phone stays stable across
   any retry of the background task.
4. **Day assignment.**  The first pick becomes day 3, the second
   becomes day 7.  When only one puzzle is available, BOTH days use
   the same one (degraded but still serves the spaced-repetition
   pedagogy — repeat exposure beats no second exposure at all).
"""

from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import chess
import yaml

from llm.seca.coach.study_plan.verdict import THEME_VOCABULARY

logger = logging.getLogger(__name__)


# Three difficulty bands aligned with ``skill_hint_for_rating`` so
# the selector can do a direct equality / adjacency check without
# remapping.
DIFFICULTY_VOCABULARY: Final[frozenset[str]] = frozenset({"beginner", "intermediate", "advanced"})


# Order matters for the adjacency check below — used to compute "one
# step adjacent" difficulty bands.
_DIFFICULTY_ORDER: tuple[str, ...] = ("beginner", "intermediate", "advanced")


# Where the YAML files live.  The library directory is co-located with
# this module so the Dockerfile's ``COPY llm/ /app/llm/`` step pulls
# the corpus into the production image without a separate manifest
# entry.
_LIBRARY_DIR: Final[Path] = Path(__file__).parent / "library"


@dataclass(frozen=True)
class LibraryPuzzle:
    """One curated training puzzle from the YAML corpus.

    All fields are validated at load time:

    * ``theme`` must be in ``THEME_VOCABULARY`` (so a typo in a YAML
      tag crashes startup, not at plan-creation time)
    * ``difficulty`` must be in ``DIFFICULTY_VOCABULARY``
    * ``fen`` must parse via ``chess.Board(fen)``
    * ``expected_move_uci`` must be a legal move in ``fen`` (so a
      puzzle that lost its expected move during edit doesn't ship
      a broken position to a player)
    """

    id: str
    theme: str
    difficulty: str
    fen: str
    expected_move_uci: str
    description: str


class LibraryValidationError(ValueError):
    """Raised when a YAML entry fails the on-load schema check.

    Carries the file path + entry id so the error message points
    directly at the offending row in the corpus."""


def load_library() -> dict[str, list[LibraryPuzzle]]:
    """Read every ``*.yaml`` file under the library directory and
    return a dict mapping theme tag → list of puzzles.

    Validation
    ----------
    Each entry must satisfy:

    * Required string fields: ``id``, ``theme``, ``difficulty``, ``fen``,
      ``expected_move_uci``, ``description``.
    * ``theme ∈ THEME_VOCABULARY``.
    * ``difficulty ∈ DIFFICULTY_VOCABULARY``.
    * ``fen`` parses to a legal position via ``chess.Board``.
    * ``expected_move_uci`` is legal in that position.
    * ``id`` is unique across the whole corpus.

    Raises ``LibraryValidationError`` on the first failure — by design,
    so a malformed corpus crashes the server at boot rather than
    silently shipping bad puzzles.  Tests pin every shipped entry
    against this loader so the YAML can't drift past CI.
    """
    library: dict[str, list[LibraryPuzzle]] = {theme: [] for theme in THEME_VOCABULARY}
    seen_ids: set[str] = set()

    if not _LIBRARY_DIR.is_dir():
        logger.warning(
            "study_plan library directory not found at %s; library empty",
            _LIBRARY_DIR,
        )
        return library

    yaml_files = sorted(_LIBRARY_DIR.glob("*.yaml"))
    for path in yaml_files:
        with path.open("r", encoding="utf-8") as fp:
            raw = yaml.safe_load(fp)
        if raw is None:
            continue
        if not isinstance(raw, list):
            raise LibraryValidationError(
                f"{path.name}: top-level must be a list of puzzle entries, "
                f"got {type(raw).__name__}"
            )
        for entry in raw:
            puzzle = _validate_entry(entry, path.name, seen_ids)
            library[puzzle.theme].append(puzzle)
            seen_ids.add(puzzle.id)

    total = sum(len(v) for v in library.values())
    logger.info(
        "study_plan library loaded: %d puzzles across %d theme buckets",
        total,
        sum(1 for v in library.values() if v),
    )
    return library


def _validate_entry(entry: object, source: str, seen_ids: set[str]) -> LibraryPuzzle:
    """Validate one raw YAML dict and return a frozen ``LibraryPuzzle``.

    Kept private + paranoid: every field gets a type check + a
    domain check before the dataclass is constructed, so the dataclass
    itself can stay declarative.
    """
    if not isinstance(entry, dict):
        raise LibraryValidationError(f"{source}: entry must be a dict, got {type(entry).__name__}")

    required = ("id", "theme", "difficulty", "fen", "expected_move_uci", "description")
    for field in required:
        if field not in entry:
            raise LibraryValidationError(
                f"{source}: entry missing required field {field!r}: {entry}"
            )
        if not isinstance(entry[field], str):
            raise LibraryValidationError(
                f"{source}: field {field!r} must be a string, "
                f"got {type(entry[field]).__name__}: {entry}"
            )

    puzzle_id = entry["id"].strip()
    theme = entry["theme"].strip()
    difficulty = entry["difficulty"].strip()
    fen = entry["fen"].strip()
    expected_move_uci = entry["expected_move_uci"].strip()
    description = entry["description"].strip()

    if puzzle_id in seen_ids:
        raise LibraryValidationError(f"{source}: duplicate puzzle id {puzzle_id!r}")
    if theme not in THEME_VOCABULARY:
        raise LibraryValidationError(
            f"{source}: puzzle {puzzle_id!r} has unknown theme {theme!r} "
            f"(not in THEME_VOCABULARY)"
        )
    if difficulty not in DIFFICULTY_VOCABULARY:
        raise LibraryValidationError(
            f"{source}: puzzle {puzzle_id!r} has unknown difficulty "
            f"{difficulty!r} (must be one of "
            f"{sorted(DIFFICULTY_VOCABULARY)})"
        )

    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise LibraryValidationError(
            f"{source}: puzzle {puzzle_id!r} FEN does not parse: {exc}"
        ) from exc

    try:
        move = chess.Move.from_uci(expected_move_uci)
    except (ValueError, chess.InvalidMoveError) as exc:
        raise LibraryValidationError(
            f"{source}: puzzle {puzzle_id!r} expected_move_uci "
            f"{expected_move_uci!r} does not parse: {exc}"
        ) from exc

    if move not in board.legal_moves:
        raise LibraryValidationError(
            f"{source}: puzzle {puzzle_id!r} expected_move_uci "
            f"{expected_move_uci!r} is not a legal move in FEN {fen!r}"
        )

    return LibraryPuzzle(
        id=puzzle_id,
        theme=theme,
        difficulty=difficulty,
        fen=fen,
        expected_move_uci=expected_move_uci,
        description=description,
    )


def pick_two_puzzles(
    library: dict[str, list[LibraryPuzzle]],
    theme: str,
    skill_hint: str,
    plan_id: str,
) -> tuple[LibraryPuzzle | None, LibraryPuzzle | None]:
    """Pick two distinct library puzzles for the day-3 / day-7 slots.

    Selection happens in three stages: theme filter, skill filter,
    deterministic shuffle.  See module docstring for the full rationale.

    Returns ``(None, None)`` when both the requested theme and the
    ``"generic"`` fallback bucket are empty.  Caller (``agent.py``)
    treats that as "leave the day-3/day-7 puzzles at the phase-1
    stub (the mistake position)" — the plan stays usable; just no
    library variants.

    When only one puzzle survives the filters, returns it as BOTH
    elements of the tuple.  That's a degraded-but-functional state:
    the user solves the same library variant on days 3 and 7, which is
    still better pedagogically than re-solving the original mistake.
    """
    # Theme filter — fall back to "generic" bucket if the requested
    # theme has no entries.
    candidates = library.get(theme, [])
    if not candidates and theme != "generic":
        candidates = library.get("generic", [])
    if not candidates:
        return (None, None)

    # Skill filter — keep puzzles whose difficulty band is exact-match
    # or one step adjacent to the player's skill_hint.  Fall through
    # to the unfiltered pool if the filter would empty the candidate
    # list.
    skill_filtered = _filter_by_skill(candidates, skill_hint)
    pool = skill_filtered if skill_filtered else candidates

    # Deterministic shuffle seeded from plan_id.  Same plan_id always
    # picks the same two puzzles, which makes the schedule stable
    # across BackgroundTask retries (a re-fired generate_plan_async
    # won't shuffle the day-3 / day-7 picks under the user).
    seed_bytes = hashlib.sha256(plan_id.encode("utf-8")).digest()
    seed_int = int.from_bytes(seed_bytes[:8], "big")
    rng = random.Random(seed_int)

    if len(pool) == 1:
        only = pool[0]
        return (only, only)

    # ``sample`` guarantees no duplicates when len(pool) >= 2.
    picks = rng.sample(pool, 2)
    return (picks[0], picks[1])


def _filter_by_skill(candidates: list[LibraryPuzzle], skill_hint: str) -> list[LibraryPuzzle]:
    """Return puzzles whose difficulty is the same band as ``skill_hint``
    or one step adjacent.

    Same-band > adjacent-band is preserved by selection order — but
    since the downstream ``random.sample`` doesn't care about order,
    we just emit the union.  A future refinement could prefer
    exact-match before adjacent."""
    if skill_hint not in _DIFFICULTY_ORDER:
        # Defensive: caller passed an unknown skill_hint.  Don't filter
        # at all; let the deterministic shuffle pick from the whole
        # candidate pool.
        return list(candidates)

    target_idx = _DIFFICULTY_ORDER.index(skill_hint)
    allowed = set()
    for idx, band in enumerate(_DIFFICULTY_ORDER):
        if abs(idx - target_idx) <= 1:
            allowed.add(band)

    return [p for p in candidates if p.difficulty in allowed]
