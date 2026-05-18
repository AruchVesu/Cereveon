"""Lichess ingestion adapter — outside the Mode-2 trust boundary.

This package imports user games + profile data from the public Lichess API
and lands them in the existing ``game_events`` table as a sibling source
to in-app gameplay (``source='lichess'`` vs ``source='app'``).

Trust-boundary note
-------------------
Lichess can return its own Stockfish evaluations when called with
``evals=true``.  These are explicitly NOT trusted by this codebase: per
``docs/ARCHITECTURE.md`` engine truth comes from the local engine pool
only.  The client therefore requests Lichess with ``evals=false`` and the
import path never copies any Lichess-derived eval into ``GameEvent``
fields that the ESV / coaching pipeline consumes.  ESV for imported games
is produced lazily by re-analysing the PGN with the local Stockfish pool
when (and only when) the user opens that game for review.
"""
