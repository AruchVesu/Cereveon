"""Mistake-replay surface.

This package owns two pieces of the post-game "replay your mistake"
loop that drives Phase 3 of the training-XP rollout:

* ``detector`` — given the AccuracyAnalysis already computed at
  /game/finish time, walk the PGN and return the FIRST player move
  whose centipawn loss clears ``MIN_MISTAKE_LOSS_CP`` (150 cp).
  Returns a ``FirstMistake`` dataclass with the FEN before that move,
  the move the player actually played, and the eval loss.  "First"
  (rather than "largest") so the surfaced lesson is the originating
  mistake, not a downstream symptom that's only big because the
  position was already lost.  The /game/finish response wire field
  is still named ``biggest_mistake`` — see the detector module
  docstring for the backward-compat rationale.

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
