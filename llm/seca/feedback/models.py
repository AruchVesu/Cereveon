"""SQLAlchemy model for user-submitted product feedback.

A ``FeedbackMessage`` row is one free-form message sent from the app's
"Send feedback" form.  Write-only from the product's perspective: the
API inserts rows; nothing in the coaching / adaptation / prompt path
ever reads them (the operator queries the table directly).  Keeping the
table append-only and out of every trust boundary is what makes this a
safe place for unconstrained user text.

Length bounds live here (not in the router) so the HTTP validator and
any future maintenance script share one source of truth.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm.seca.auth.models import Base, Player

# Upper bound on the message body, enforced at the HTTP boundary.  Long
# enough for a detailed bug report; small enough that a malicious client
# cannot use the form as blob storage.
MAX_FEEDBACK_MESSAGE_LEN: int = 2000

# Upper bound on the optional client-reported app version string
# (``BuildConfig.VERSION_NAME`` on Android — "1.4.2" shaped, never long).
MAX_APP_VERSION_LEN: int = 64


class FeedbackMessage(Base):
    __tablename__ = "feedback_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.id"), nullable=False, index=True
    )

    # Free-form user text, stored verbatim (already length-capped and
    # whitespace-trimmed by the router's request validator).  ``Text``
    # rather than ``String`` so Postgres doesn't need a length migration
    # if MAX_FEEDBACK_MESSAGE_LEN is ever raised.
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Client-reported app version ("1.4.2").  Nullable — older clients
    # and non-Android callers may omit it.
    app_version: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player")
