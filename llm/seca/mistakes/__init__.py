"""Mistake-replay surface.

This package owns two pieces of the post-game "replay your worst move"
loop that drives Phase 3 of the training-XP rollout:

* ``detector`` — given the AccuracyAnalysis already computed at
  /game/finish time, find the position where the player made their
  largest centipawn loss.  Returns a ``BiggestMistake`` dataclass with
  the FEN before that move, the move the player actually played, and
  the eval loss.  Costs at most one extra engine call (a re-analysis
  of the mistake position to surface the engine's preferred move at
  context-display time).

* ``verify`` + ``router`` — ``POST /training/verify-replay`` accepts
  ``{fen, move_uci}`` from the Android replay sheet, runs the Stockfish
  pool, and answers whether the user's move is within 30 cp of the
  engine's best move.  Used by the replay UI before it calls
  ``POST /training/solve`` (Phase 2) on a verified-correct attempt.

Trust boundary
--------------
The verifier is the trust anchor for the XP credit path.  The Android
client could in principle skip the verifier and call /training/solve
directly with ``source_type=mistake_replay``, but the /training/solve
endpoint will accept any well-formed request — credibility is enforced
upstream of it.  Phase 3 has both the client and the server check the
move; Phase 4+ could tighten by requiring a server-issued nonce that
proves the client actually went through /training/verify-replay.
"""
