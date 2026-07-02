import uuid
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
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

    # Monotonic counter incremented when the player completes a
    # training exercise (seed = replay of their own engine-flagged
    # mistake; derivatives = weekly micro-tasks generated from the
    # mistake pattern).  Surfaced via /auth/me so the Android Home
    # screen can render a Level/XP card in place of the hidden Elo
    # rating.  Rating + confidence stay on the row because they still
    # drive adaptive opponent selection internally — only the UI
    # surface changes.
    training_xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # OAuth identity for "Sign in with Lichess" (POST /auth/lichess): the
    # canonical lowercase Lichess user id, verified server-side via the
    # authorization-code exchange + GET /api/account.  NULL for password
    # accounts.  Unique so one Lichess identity maps to exactly one player;
    # ``unique + index`` yields the conventionally-named
    # ``ix_players_lichess_user_id`` unique index, matching the idempotent
    # DDL that ``init_schema`` emits for pre-existing tables (SQLite's
    # ALTER TABLE ADD COLUMN cannot carry UNIQUE, so the migration adds a
    # plain column + this index).
    lichess_user_id: Mapped[str | None] = mapped_column(
        String, unique=True, index=True, nullable=True
    )

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

    # sha256 of the PREVIOUS JWT, still valid until [previous_token_expires_at].
    # Rationale: under per-token rotation, two concurrent authenticated
    # requests with the same starting token both rotate server-side;
    # whichever request reaches `rotate_session_token` second sends a
    # now-revoked token (the first request rotated past it) and 401s.
    # Holding the previous hash for a brief grace window
    # (AuthService.PREVIOUS_TOKEN_GRACE_SECONDS) accepts both tokens
    # during the race window, eliminating the cascade without
    # meaningfully weakening F-07: a stolen JWT still becomes useless
    # within seconds of the legitimate owner's next call.  Both columns
    # nullable so pre-grace-window rows / sessions without a previous
    # rotation pass through cleanly.
    previous_token_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    previous_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.utcnow() + timedelta(days=7),
        index=True,
    )
    device_info: Mapped[str] = mapped_column(String, default="")

    player: Mapped["Player"] = relationship("Player", back_populates="sessions")
