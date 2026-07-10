"""Post-game AI review for imported Lichess games.

Turns one imported ``source='lichess'`` :class:`~llm.seca.events.models.GameEvent`
into a three-wave review the Android client renders progressively:

* Wave 1 — game metadata (client-local, from ``GET /game/history`` plus
  PGN headers echoed by this package's GET payload).
* Wave 2 — engine truth: banded per-position eval series, per-move
  classification counts, accuracy, and the top-3 *critical moments*
  selected deterministically by :mod:`llm.seca.review.moments`.
* Wave 3 — coach commentary: per-moment explanations plus a
  pattern-level verdict written by :mod:`llm.seca.review.writer`
  through the same Mode-2 trust gates every other LLM surface uses.

Layering
--------
``models`` (ORM row) ← ``moments`` (pure selection math) ←
``writer`` (LLM realization, untrusted → validated) ←
``service`` (job orchestration) ← ``router`` (HTTP).

Trust posture mirrors ``docs/ARCHITECTURE.md``: engine output is the
source of truth; the LLM only realizes deterministic, coordinate-free
facts and every reply passes ``validate_mode_2_negative`` plus the
output firewall or is replaced by a deterministic fallback.  Raw
centipawns never leave the server — the HTTP payload carries ESV bands
only, so the client *cannot* render numeric evals (the Atrium
no-numeric-eval invariant is enforced at the wire, not by client
discipline).
"""
