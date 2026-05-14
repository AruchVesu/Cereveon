"""Server-side chat history persistence.

Per-player chat turns are saved by the ``/chat`` + ``/chat/stream``
handlers (auto-fired after boundary validation succeeds) so a user's
conversation with the coach survives device swaps, app reinstalls, and
process restarts.  The Android client preloads recent turns on chat-sheet
open via the companion ``GET /chat/history`` endpoint.

Strictly deterministic: this layer stores ROLE + CONTENT + FEN +
created_at.  It does NOT update player skill / rating / weakness
state — those are owned by ``llm.seca.skills.updater.SkillUpdater``
and triggered only by ``/game/finish`` per the SECA architecture's
"no autonomous learning" rule.
"""
