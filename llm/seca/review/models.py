"""SQLAlchemy model for one post-game review of an imported game.

One ``GameReview`` row is both the review *job* (its ``status`` column
drives the client's 2s poll, mirroring ``LichessImportJob``) and the
review *result* (the three JSON payload columns).  Collapsing job and
result into one row keeps the polling contract trivial: the client
polls ``GET /game/{event_id}/review`` and renders whatever stages have
landed — there is no separate job id to correlate.

Persistence shape
-----------------
The three payload columns are JSON-in-TEXT (not Postgres JSONB) for
SQLite/Postgres parity — the whole schema stays portable to the test
runs, matching ``GameFinishResult.response_json``:

* ``engine_json``  — Wave 2: banded eval series, classification counts,
  accuracy band, PGN-header metadata.  Written when the engine stage
  completes.
* ``moments_json`` — the selected critical moments (see
  ``llm.seca.review.moments.CriticalMoment.to_payload``).  Written with
  ``engine_json`` (selection is pure math on the engine output).
* ``llm_json``     — Wave 3: per-moment coach texts + verdict, or the
  reason the LLM stage was skipped (entitlement) / degraded (provider
  failure → deterministic fallback texts).

``UNIQUE (game_event_id, analysis_version)`` is the cache key from the
feature spec: re-opening a reviewed game is a pure DB read; bumping
``ANALYSIS_VERSION`` (when selection logic or payload shape changes)
makes the next POST create a fresh row while the old one stays for
history.

Status model
------------
``queued`` → ``running`` → ``engine_done`` → ``complete`` | ``failed``

``engine_done`` is the Wave-2 checkpoint: engine + moments payloads are
readable while the LLM stage is still in flight.  ``complete`` is
terminal and does NOT imply LLM success — ``llm_json`` distinguishes
full, skipped (entitlement), and fallback outcomes.  ``failed`` means
the ENGINE stage failed; there is nothing renderable.  String column
(not Enum) for dialect parity, same rationale as ``LichessImportJob``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm.seca.auth.models import Base, Player
from llm.seca.events.models import GameEvent

#: Bump when moment-selection logic or payload shape changes in a way
#: that should invalidate stored reviews.  The POST endpoint only ever
#: reads/creates rows at this version; older rows stay for history.
ANALYSIS_VERSION = 1

REVIEW_STATUS_QUEUED = "queued"
REVIEW_STATUS_RUNNING = "running"
REVIEW_STATUS_ENGINE_DONE = "engine_done"
REVIEW_STATUS_COMPLETE = "complete"
REVIEW_STATUS_FAILED = "failed"

REVIEW_STATUS_TERMINAL = frozenset({REVIEW_STATUS_COMPLETE, REVIEW_STATUS_FAILED})
REVIEW_STATUS_ACTIVE = frozenset(
    {REVIEW_STATUS_QUEUED, REVIEW_STATUS_RUNNING, REVIEW_STATUS_ENGINE_DONE}
)

#: Wave-3 outcome discriminators persisted inside ``llm_json`` under the
#: ``"outcome"`` key.  ``skipped_entitlement`` is the free-tier cap
#: (client renders the upgrade CTA); ``fallback`` means the provider or
#: validators rejected every attempt and the deterministic texts were
#: served instead ("Try coach review again" stays available because the
#: POST endpoint re-runs the LLM stage for fallback outcomes).
LLM_OUTCOME_FULL = "full"
LLM_OUTCOME_FALLBACK = "fallback"
LLM_OUTCOME_SKIPPED_ENTITLEMENT = "skipped_entitlement"


class GameReview(Base):
    __tablename__ = "game_reviews"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    game_event_id: Mapped[str] = mapped_column(
        String, ForeignKey("game_events.id"), nullable=False, index=True
    )
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.id"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(String, nullable=False, default=REVIEW_STATUS_QUEUED)

    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False, default=ANALYSIS_VERSION)

    #: "standard" | "strategic" — strategic is the spec's softer-language
    #: mode for games with no natural critical moments.  NULL until the
    #: engine stage selects moments.
    review_mode: Mapped[str | None] = mapped_column(String, nullable=True)

    engine_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    moments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: First 500 chars of the exception that failed the ENGINE stage
    #: (truncated at write time to bound hostile/pathological messages).
    #: LLM-stage trouble never lands here — it degrades into ``llm_json``.
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    player: Mapped["Player"] = relationship("Player")
    event: Mapped["GameEvent"] = relationship("GameEvent")

    __table_args__ = (
        UniqueConstraint(
            "game_event_id",
            "analysis_version",
            name="uq_game_reviews_event_version",
        ),
    )
