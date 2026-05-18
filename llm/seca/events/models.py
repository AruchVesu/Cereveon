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
    # "Imported games") and used by the lazy re-analysis path to decide
    # whether ESV must be recomputed from PGN.
    source: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # External-platform game identifier when ``source != 'app'`` — the
    # Lichess game ID for ``source='lichess'``.  Used together with
    # ``source`` for dedup so re-importing the same window does not
    # create duplicate rows.  NULL for in-app games.
    external_game_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player")

    # Composite uniqueness on (source, external_game_id) keeps dedup
    # per-source so a future chess.com importer cannot collide with
    # Lichess on the bare 8-char ID space.  NULL pairs (in-app games)
    # are exempt by ANSI semantics on both SQLite and Postgres.
    __table_args__ = (
        UniqueConstraint("source", "external_game_id", name="uq_game_events_source_external_id"),
    )
