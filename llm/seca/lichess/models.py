"""SQLAlchemy model for the player ↔ external chess-platform link.

A row in ``linked_accounts`` represents one external-platform account
(currently only ``platform='lichess'``) linked to one ChessCoach player.
The ``platform`` column is future-proofed for ``chess.com`` / etc.; we
accept the small storage cost now to avoid a second migration later.

Constraints
-----------
``(player_id, platform)`` is unique: one Lichess link per player.  A
second link request with a different username overwrites — handled at
the service layer by deleting + re-inserting so calibration re-fires.

``(platform, external_username)`` is unique: one Lichess account can be
attached to at most one ChessCoach player at a time.  If user B tries to
link a handle user A already owns, the service layer returns 409 instead
of a 500 from the constraint violation.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm.seca.auth.models import Base, Player


class LinkedAccount(Base):
    __tablename__ = "linked_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.id"), nullable=False, index=True
    )
    # 'lichess' for MVP; future: 'chesscom', etc.
    platform: Mapped[str] = mapped_column(String, nullable=False)
    external_username: Mapped[str] = mapped_column(String, nullable=False)

    # Watermark used as ``since=`` on the next incremental fetch.  None
    # until the first successful import completes.  We persist the
    # timestamp of the newest game seen rather than ``now()`` so a
    # paused import doesn't silently skip games created during the
    # outage window.
    last_imported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player")

    __table_args__ = (
        UniqueConstraint("player_id", "platform", name="uq_linked_accounts_player_platform"),
        UniqueConstraint(
            "platform", "external_username", name="uq_linked_accounts_platform_username"
        ),
    )
