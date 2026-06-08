"""SQLAlchemy model for the per-player chat history table.

Schema
------
One row per coaching turn (user or assistant).  The pair of rows that
makes up a single ``/chat`` exchange shares a ``created_at`` ordering
window — the persistence layer writes the user message first, then the
assistant reply, in a single transaction, so a ``ORDER BY created_at``
query reconstructs the exchange in conversational order.

Why a single table rather than per-conversation aggregation
-----------------------------------------------------------
The Cereveon coach has no concept of "conversations" — the LLM is
re-grounded on the current ``FEN`` every turn (see
``docs/ARCHITECTURE.md`` § "/chat Endpoint — Per-Turn Grounding
Semantics").  Server-side persistence mirrors that model: the unit is a
single turn, optionally annotated with the ``fen`` that was in scope
when it landed, and the client decides how many recent turns to load
into context.

Foreign keys
------------
``player_id`` references ``players.id`` so a ``DELETE`` of the player
row cascades the chat history out with it (privacy + GDPR scope is
satisfied by deleting the player).

Indexes
-------
``ix_chat_turn_player_created`` covers the player-global
``GET /chat/history?limit=N`` query
(``WHERE player_id = ? ORDER BY created_at DESC LIMIT N``).
``ix_chat_turn_player_game_created`` covers the per-game variant
(``... AND game_id = ? ...``) used when the client scopes history to a
game (see the ``game_id`` column).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from llm.seca.auth.models import Base


class ChatTurn(Base):
    """One persisted chat message — user question or assistant reply."""

    __tablename__ = "chat_turns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.id"), index=True, nullable=False
    )

    # Game this turn belongs to (``games.id``) so chat history is scoped
    # per game — each game is its own thread. Nullable on purpose: turns
    # saved outside an active game (or before this column existed) are
    # player-global (the legacy behaviour) and surface when no game_id is
    # requested. Intentionally NOT a hard ForeignKey — chat must survive a
    # ``games`` row being pruned; the ``player_id`` FK already covers the
    # GDPR delete-cascade. ``player_id`` stays the isolation boundary, so a
    # stray game_id only reshuffles a player's OWN grouping.
    game_id: Mapped[str | None] = mapped_column(String)

    # ``user`` / ``assistant`` / ``system``.  Mirrors the
    # ``ChatTurn`` dataclass in ``llm.seca.coach.chat_pipeline`` —
    # the same shape is used in the pipeline's in-memory message
    # list, so loading rows from the DB into the prompt context is
    # zero-copy on the role field.
    role: Mapped[str] = mapped_column(String(16), nullable=False)

    # The full message text as it appeared to the user.  For
    # ``assistant`` rows this is exactly what the chat bubble
    # rendered — including the deterministic-fallback wording when
    # the boundary validator rejected an LLM reply (see
    # docs/ARCHITECTURE.md § Deterministic Fallback).  No truncation
    # at the storage layer; the in-pipeline 50-turn cap +
    # 20-turn compaction in ``chat_pipeline.context_compact`` keep
    # the working set bounded.
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # ``FEN`` at the moment of this turn — supplied for ``user``
    # turns from the client request, mirrored onto the paired
    # ``assistant`` row so a history replay can reconstruct the
    # position context for any turn.  Nullable for ``system`` rows
    # (compaction summaries injected by ``context_compact``) and
    # for rows whose origin is a future endpoint that lacks a FEN.
    fen: Mapped[str | None] = mapped_column(Text)

    # Coaching mode that produced this turn.  Always ``"CHAT_V1"``
    # for current ``/chat`` and ``/chat/stream`` saves.  Kept as a
    # column rather than a constant so the storage schema doesn't
    # need a migration when a new mode (e.g. ``CHAT_V2``) ships.
    mode: Mapped[str] = mapped_column(String(16), default="CHAT_V1")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True, nullable=False
    )

    __table_args__ = (
        Index("ix_chat_turn_player_created", "player_id", "created_at"),
        # Covers the per-game query
        # (WHERE player_id = ? AND game_id = ? ORDER BY created_at DESC).
        Index("ix_chat_turn_player_game_created", "player_id", "game_id", "created_at"),
    )
