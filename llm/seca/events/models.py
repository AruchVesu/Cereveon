import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm.seca.auth.models import Base, Player


class GameEvent(Base):
    __tablename__ = "game_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    player_id: Mapped[str | None] = mapped_column(String, ForeignKey("players.id"), index=True)

    # raw PGN or compact move list
    pgn: Mapped[str] = mapped_column(Text, nullable=False)

    # result: win / loss / draw
    result: Mapped[str] = mapped_column(String, nullable=False)

    # engine accuracy / centipawn loss etc.
    accuracy: Mapped[float | None] = mapped_column(Float, default=0.0)

    # detected weaknesses JSON
    weaknesses_json: Mapped[str | None] = mapped_column(Text, default="{}")

    # Provenance for the row.  ``'app'`` (or NULL, legacy) means the game
    # was played in the ChessCoach Android client and finalised via
    # ``/game/finish``; ``'lichess'`` means it was pulled from the Lichess
    # public API by the import service.  Filterable for UI tabs (e.g.
    # "Imported games") and used by the post-import engine-analysis pass
    # (llm.seca.lichess.analysis_service) to select the imported rows
    # whose accuracy / weaknesses still need computing from PGN.
    source: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # External-platform game identifier when ``source != 'app'`` — the
    # Lichess game ID for ``source='lichess'``.  Used together with
    # ``source`` for dedup so re-importing the same window does not
    # create duplicate rows.  NULL for in-app games.
    external_game_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # In-app game id (the ``games.id`` minted by /game/start) this finished
    # row corresponds to, captured from ``GameFinishRequest.game_id`` at
    # /game/finish.  Distinct from ``external_game_id`` (reserved for external
    # platforms like Lichess, NULL for in-app games): this links a history row
    # back to the live game's chat thread (``chat_turns.game_id``) so the
    # game-history UI can surface each game's coaching conversation.  NULL for
    # legacy rows, imported games, and finishes from older clients that never
    # sent a game_id — those simply have no per-game chat to show.  Not
    # indexed: it is projection-only here (the chat lookup filters
    # ``chat_turns.game_id``, which carries its own covering index).
    app_game_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player")

    # Composite uniqueness on (source, external_game_id) keeps dedup
    # per-source so a future chess.com importer cannot collide with
    # Lichess on the bare 8-char ID space.  NULL pairs (in-app games)
    # are exempt by ANSI semantics on both SQLite and Postgres.
    __table_args__ = (
        UniqueConstraint("source", "external_game_id", name="uq_game_events_source_external_id"),
    )


class GameFinishResult(Base):
    """Persisted response payload for one ``POST /game/finish`` call.

    Today this exists purely so ``GET /game/finish/{event_id}/status``
    can return the same body the synchronous POST already returned —
    useful for client retry recovery when a slow mobile network drops
    the response after the server already committed.  Storing the
    payload here also lays the foundation for a future PR that moves
    the ~2 s Stockfish recompute off the POST hot path: the recompute
    worker will write to this table instead, and the GET endpoint will
    return ``{status: "pending"}`` until the row exists.

    The ``response_json`` column stores the full assistant-visible
    response body as a JSON string — same shape as what ``finish_game``
    returns today.  Storing as JSON-in-TEXT (rather than Postgres
    JSONB) keeps the schema portable to the SQLite test runs.  Row
    width is bounded by the response shape (coach_content + analysis +
    biggest_mistake), typically well under 4 KB.
    """

    __tablename__ = "game_finish_results"

    event_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("game_events.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Full response payload from the POST /game/finish call that
    # produced this event, encoded as JSON.  ``Text`` (not String)
    # because the typical row is several KB; Postgres has no length
    # limit on TEXT and SQLite treats both interchangeably.
    response_json: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)

    # Eager relationship for the rare admin/debug query that wants the
    # GameEvent + its result together.  Routine reads go directly via
    # ``event_id`` so we don't pay the join cost.
    event: Mapped["GameEvent"] = relationship("GameEvent")
