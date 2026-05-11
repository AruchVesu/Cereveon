import uuid
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base for every SECA model.

    Switched from the legacy ``declarative_base()`` factory to a typed
    ``DeclarativeBase`` subclass in Sprint 6.A so the auth-layer
    attribute types flow through to mypy.  Subclasses (events, brain,
    analytics, storage, curriculum, ...) keep their existing
    ``Column(...)`` class-level definitions — SQLAlchemy 2.x supports
    mixed legacy and ``mapped_column()`` declarations on the same base.
    """


class Player(Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    rating: Mapped[float] = mapped_column(Float, default=1200.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    skill_vector_json: Mapped[str] = mapped_column(Text, default="{}")
    player_embedding: Mapped[str] = mapped_column(Text, default="[]")

    sessions: Mapped[list["Session"]] = relationship("Session", back_populates="player")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str | None] = mapped_column(String, ForeignKey("players.id"), index=True)

    # sha256 of the LATEST JWT issued for this session.  Rotated on
    # every successful authenticated call (router.get_current_player ->
    # AuthService.rotate_session_token) so a previously-issued JWT
    # immediately becomes unusable once a fresher one is minted —
    # closes the F-07 "stolen JWT lives until exp (24 h)" gap.
    # Nullable on the column so legacy rows created before this column
    # existed don't break SELECTs; new rows always populate it on
    # login(), and rotate_session_token() never writes NULL.
    token_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.utcnow() + timedelta(days=7),
        index=True,
    )
    device_info: Mapped[str] = mapped_column(String, default="")

    player: Mapped["Player"] = relationship("Player", back_populates="sessions")
