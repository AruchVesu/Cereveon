"""Persistence helpers for the chat-history table.

Thin wrappers over the SQLAlchemy session — separated from the route
handler so the persistence contract (two rows per exchange, ordered by
``created_at``) is testable in isolation and the handler stays
focussed on the LLM pipeline + boundary validation.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as DBSession

from llm.seca.chat.models import ChatTurn

# Hard ceiling on the ``GET /chat/history`` query.  Mirrors the
# 50-turn ``ChatRequest.messages`` cap on the wire (see
# ``llm/server.py::ChatRequest.validate_messages``) so the client
# can preload up to the request-time cap without exceeding it.
HISTORY_DEFAULT_LIMIT = 50
HISTORY_MAX_LIMIT = 200


def save_exchange(
    db: DBSession,
    player_id: str,
    user_content: str,
    assistant_content: str,
    fen: str,
    mode: str = "CHAT_V1",
    game_id: str | None = None,
) -> None:
    """Persist one ``/chat`` exchange (user message + assistant reply).

    Both rows commit in a single transaction so a partial save (only the
    user turn, or only the assistant reply) is impossible — a crash
    between rows leaves the table in the same state as a complete
    rollback.  The route handler calls this AFTER ``validate_chat_response``
    succeeds; the contract is "save what the user saw", so the
    ``assistant_content`` is the final reply that landed in the bubble
    (deterministic fallback included when the safety net replaced an
    LLM reply that failed the boundary validator — see
    ``server.py::chat``).

    ``game_id`` scopes the exchange to a game (each game its own thread);
    ``None`` keeps it player-global (legacy behaviour). It is mirrored onto
    both rows so the pair stays in one thread.
    """
    db.add_all(
        [
            ChatTurn(
                player_id=player_id,
                game_id=game_id,
                role="user",
                content=user_content,
                fen=fen,
                mode=mode,
            ),
            ChatTurn(
                player_id=player_id,
                game_id=game_id,
                role="assistant",
                content=assistant_content,
                fen=fen,
                mode=mode,
            ),
        ]
    )
    db.commit()


def recent_turns_for_player(
    db: DBSession,
    player_id: str,
    limit: int = HISTORY_DEFAULT_LIMIT,
    game_id: str | None = None,
) -> list[ChatTurn]:
    """Return the most-recent ``limit`` turns for ``player_id``.

    Order is DESC (newest first); the route handler reverses to
    chronological for client-side consumption.  Cross-player isolation
    is by ``WHERE player_id = ?`` filter — the route is gated on
    ``get_current_player`` so the inbound ``player_id`` is the
    authenticated player.  ``limit`` is clamped to [1, HISTORY_MAX_LIMIT]
    so a malformed client request can't issue an unbounded scan.

    ``game_id`` scopes the result to a single game's thread when provided;
    ``None`` returns the player-global history (every turn, all games +
    untied), preserving the legacy behaviour for callers that don't pass
    a game. ``player_id`` is always applied, so it stays the isolation
    boundary regardless of ``game_id``.
    """
    bounded = max(1, min(limit, HISTORY_MAX_LIMIT))
    query = db.query(ChatTurn).filter(ChatTurn.player_id == player_id)
    if game_id is not None:
        query = query.filter(ChatTurn.game_id == game_id)
    return query.order_by(ChatTurn.created_at.desc(), ChatTurn.id.desc()).limit(bounded).all()
