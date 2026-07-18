"""SQLAlchemy model for a user report of AI-generated coach content.

A ``ContentReport`` row is one flag raised through the in-app "Report"
affordance on a coach message (Google Play AI-Generated Content policy).
Write-only from the product's perspective: the API inserts rows, and
nothing in the coaching / adaptation / prompt path ever reads them — the
operator queries the table directly to inform moderation and filtering.
Keeping it append-only and out of every trust boundary is what makes it
a safe place for the (untrusted, user-flagged) coach text.

Length bounds live here so the HTTP validator and any maintenance script
share one source of truth.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm.seca.auth.models import Base, Player

# The reported coach message.  Generous cap: chat replies are short, but
# a game-review verdict can run longer; still small enough that the
# endpoint can't be abused as blob storage.
MAX_REPORT_CONTENT_LEN: int = 8000

# Optional free-text reason the user adds ("this is offensive because…").
MAX_REPORT_REASON_LEN: int = 1000

# FEN context is short by construction.
MAX_REPORT_FEN_LEN: int = 200

# Where the reported content appeared.  A closed vocabulary validated at
# the HTTP boundary; ``String`` column (not Enum) for SQLite/Postgres
# parity, same reasoning as ``chat_turns.mode``.
REPORT_SURFACES: frozenset[str] = frozenset({"chat", "live_move", "review", "study_plan", "other"})


class ContentReport(Base):
    __tablename__ = "content_reports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # The AI-generated coach text the user flagged, stored verbatim
    # (length-capped + trimmed at the router).  ``Text`` so raising the
    # cap needs no Postgres migration.
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # One of ``REPORT_SURFACES`` — which coach surface produced it.
    surface: Mapped[str] = mapped_column(String(32), nullable=False)

    # Board position the content referred to, when the surface has one.
    fen: Mapped[str | None] = mapped_column(String, nullable=True)

    # Optional user-supplied reason.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Operator moderation-queue marker: 0 = unreviewed, 1 = reviewed.
    # Default 0 so a fresh report is always in the queue.
    reviewed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player")
