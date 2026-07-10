"""Standalone puzzle-trainer surface (Puzzles tab).

This package owns the HTTP layer that feeds the Android Puzzles tab
with an endless stream of practice puzzles, decoupled from the
per-mistake study plan (which drills a specific position from the
player's own game).

* ``router`` — ``GET /puzzles/next`` serves one practice puzzle:
  live-fetched from Lichess's public puzzle database
  (``GET /api/puzzle/next``, "mix" angle) at a difficulty band derived
  from the player's rating, falling back to the curated local corpus
  (``llm/seca/coach/study_plan/library/*.yaml``) on any Lichess
  failure or when ``PUZZLES_LICHESS_ENABLED=0``.

Solve loop (client-side)
------------------------
The trainer terminates at the same two endpoints every other training
source uses:

1. ``POST /training/verify-replay`` — the LOCAL engine judges the
   attempt (trust anchor; the Lichess solution move is a display /
   short-circuit hint only, never a correctness oracle).
2. ``POST /training/solve`` with ``source_type="standard_puzzle"`` and
   ``source_ref=<puzzle_id>`` — credits XP, deduped per puzzle by the
   ``(player, source_type, source_ref)`` unique triple.

Trust boundary
--------------
The fetched puzzle is a training POSITION only.  Lichess evaluations
are never requested or propagated (see ``llm/seca/lichess/client.py``
> ``fetch_puzzle_by_theme`` and ``docs/ARCHITECTURE.md``), and nothing
in this package touches the LLM / ESV pipeline.
"""
