"""Closed vocabulary for ESV tactical_flags and position_flags.

The ESV (Engine Signal Vector) carries two open-shaped lists,
``tactical_flags`` and ``position_flags``, defined in
``docs/ARCHITECTURE.md`` (§72-95) as the *only* place the system may
surface board-derived categorical information to the LLM.

This module is the single source of truth for the *allowed values* in
those lists.  Every label that may appear in either list is enumerated
here; any incoming value not in the corresponding set is dropped by
``extract_engine_signal`` with a warning, so a hostile or buggy
upstream cannot smuggle arbitrary strings (prompt-injection bait,
move suggestions disguised as flags, free-form numbers) into the
Mode-2 prompt.

Architecture invariants this respects (verbatim from ARCHITECTURE.md):

  "No numeric precision beyond coarse bands.  No move lists.  No
  search metadata.  Forbidden: Raw engine output, Principal
  variations, Depth, nodes, or scores."

Every entry in the two frozensets below is a categorical label —
either a bare category name (``hanging_piece``) or a colon-suffixed
qualifier (``king_safety:white_exposed``).  No square coordinates, no
piece-and-destination tuples, no numeric precision.  Adding a new
label means adding it here AND populating it from
``llm.seca.engines.stockfish.board_features`` (or the adapter); the
ESV extractor will not let an unknown label through.

Categories
----------

``TACTICAL_FLAGS_VOCAB`` — transient threats / immediate facts that
hold for *the current move only*:

  ``hanging_piece`` — at least one side has a piece undefended-and-attacked
                     (kept for backwards compatibility with
                     pre-PR fixtures; prefer the side-qualified form)
  ``hanging_piece:white`` / ``hanging_piece:black``
  ``check:white_to_move`` / ``check:black_to_move``

``POSITION_FLAGS_VOCAB`` — slower-moving structural features of the
position:

  ``space_advantage`` / ``space_advantage:white`` / ``space_advantage:black``
  ``king_safety:{side}_{band}`` where side ∈ {white, black},
    band ∈ {exposed, loose, safe}
  ``pawn_structure:{side}_{feature}`` where feature ∈
    {doubled, isolated, passed}
  ``castling:{side}_{state}`` where state ∈
    {kingside_done, queenside_done, uncastled}
  ``material:{state}`` where state ∈
    {even, white_up_pawn, white_up_piece, white_up_major,
     black_up_pawn, black_up_piece, black_up_major}

Adding a label is a four-step change: (1) add the string here, (2)
populate it from ``board_features.py``, (3) regenerate the prompt
golden snapshots that change, (4) confirm
``test_violations_corpus.py`` still pins every advertised forbidden
behavior.  Validators do NOT need to know the label set — they gate
on ``tactical_flags == []`` for tactical vocabulary; the *content* of
the list is opaque to them.
"""

from __future__ import annotations


def _king_safety_set() -> frozenset[str]:
    return frozenset(
        f"king_safety:{side}_{band}"
        for side in ("white", "black")
        for band in ("exposed", "loose", "safe")
    )


def _pawn_structure_set() -> frozenset[str]:
    return frozenset(
        f"pawn_structure:{side}_{feature}"
        for side in ("white", "black")
        for feature in ("doubled", "isolated", "passed")
    )


def _castling_set() -> frozenset[str]:
    return frozenset(
        f"castling:{side}_{state}"
        for side in ("white", "black")
        for state in ("kingside_done", "queenside_done", "uncastled")
    )


def _material_set() -> frozenset[str]:
    return frozenset(
        "material:" + state
        for state in (
            "even",
            "white_up_pawn",
            "white_up_piece",
            "white_up_major",
            "black_up_pawn",
            "black_up_piece",
            "black_up_major",
        )
    )


TACTICAL_FLAGS_VOCAB: frozenset[str] = frozenset(
    (
        "hanging_piece",  # backwards-compat unqualified form
        "hanging_piece:white",
        "hanging_piece:black",
        "check:white_to_move",
        "check:black_to_move",
    )
)


POSITION_FLAGS_VOCAB: frozenset[str] = frozenset(
    (
        "space_advantage",  # backwards-compat unqualified form
        "space_advantage:white",
        "space_advantage:black",
    )
) | _king_safety_set() | _pawn_structure_set() | _castling_set() | _material_set()


__all__ = (
    "TACTICAL_FLAGS_VOCAB",
    "POSITION_FLAGS_VOCAB",
)
