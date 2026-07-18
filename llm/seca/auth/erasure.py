"""Account erasure — the GDPR Article 17 deletion authority.

``purge_player_data`` deletes EVERY row linked to one player across the
whole SECA schema, children before parents, then the ``players`` row
itself, in a single transaction.  It is the implementation behind
``DELETE /auth/me`` (llm/seca/auth/router.py) and the only sanctioned
way to erase an account.

Why explicit ordered deletes instead of relying on ``ON DELETE CASCADE``
------------------------------------------------------------------------
The model FKs do declare ``ondelete="CASCADE"`` (and ``init_schema``
retrofits the constraint onto live Postgres tables), but DB-level
cascade alone cannot carry erasure:

* SQLite only honours FK actions when ``PRAGMA foreign_keys=ON`` is set
  per-connection, which this codebase does not do — dev/test databases
  would silently orphan every child row.
* Three tables carry a plain ``player_id`` column with NO FK constraint
  at all (``bandit_experiences``, ``bandit_weights``,
  ``training_decisions``) — no cascade can ever reach them.

The explicit plan below is therefore the source of truth; the DB-level
cascade is defence in depth for operator-driven deletes (e.g. a manual
``DELETE FROM players`` in psql).

Keeping the plan complete
-------------------------
``llm/tests/test_auth_account_deletion.py`` discovers every
player-linked table from ``Base.metadata`` (any ``player_id`` column,
or any FK path that transitively reaches ``players``) and fails when a
table is missing from ``ERASED_TABLES`` — adding a new player-linked
model without registering it here breaks CI, not production.

Layering: this module imports model classes only — no routers, no
services, no engine code (pinned by test_seca_layer_boundaries.py's
auth-directory sweep).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import MetaData, select
from sqlalchemy.orm import Session as DBSession

from llm.seca.analytics.models import AnalyticsEvent, WeeklyDigest
from llm.seca.auth.models import Base, Player, Session
from llm.seca.brain.models import BanditExperience, ConfidenceUpdate, RatingUpdate
from llm.seca.brain.training.models import TrainingDecision, TrainingOutcome
from llm.seca.chat.models import ChatTurn
from llm.seca.coach.study_plan.models import MistakeStudyPlan, MistakeStudyPuzzle
from llm.seca.curriculum.models import TrainingPlan
from llm.seca.entitlements.models import UsageCounter
from llm.seca.events.models import GameEvent, GameFinishResult
from llm.seca.feedback.models import FeedbackMessage
from llm.seca.lichess.models import LichessImportJob, LinkedAccount
from llm.seca.moderation.models import ContentReport
from llm.seca.notifications.models import Notification
from llm.seca.review.models import GameReview
from llm.seca.storage.models import BanditWeights, Explanation, Game, Move, Repertoire
from llm.seca.training.models import TrainingCompletion

logger = logging.getLogger(__name__)


def _erasure_plan(player_id: str) -> list[tuple[type[Base], Any]]:
    """Ordered ``(model, delete-criterion)`` pairs for one player.

    Order is FK-safe: rows keyed through a parent table (and therefore
    lacking a ``player_id`` of their own) are deleted first, while their
    parent rows still exist to anchor the ``IN (SELECT ...)`` subquery;
    direct children follow; ``game_events`` goes after the tables that
    reference it (``game_reviews`` / ``mistake_study_plans``); the
    ``players`` row is always last.
    """
    game_ids = select(Game.id).where(Game.player_id == player_id)
    event_ids = select(GameEvent.id).where(GameEvent.player_id == player_id)
    plan_ids = select(MistakeStudyPlan.id).where(MistakeStudyPlan.player_id == player_id)
    decision_ids = select(TrainingDecision.id).where(TrainingDecision.player_id == player_id)
    return [
        # Grandchildren reached through a parent table.
        (GameFinishResult, GameFinishResult.event_id.in_(event_ids)),
        (RatingUpdate, RatingUpdate.event_id.in_(event_ids)),
        (ConfidenceUpdate, ConfidenceUpdate.event_id.in_(event_ids)),
        (MistakeStudyPuzzle, MistakeStudyPuzzle.plan_id.in_(plan_ids)),
        (Move, Move.game_id.in_(game_ids)),
        (Explanation, Explanation.game_id.in_(game_ids)),
        (TrainingOutcome, TrainingOutcome.decision_id.in_(decision_ids)),
        # Direct children.  game_reviews + mistake_study_plans carry FKs
        # into game_events, so they must precede its delete below.
        (GameReview, GameReview.player_id == player_id),
        (MistakeStudyPlan, MistakeStudyPlan.player_id == player_id),
        (ChatTurn, ChatTurn.player_id == player_id),
        (AnalyticsEvent, AnalyticsEvent.player_id == player_id),
        (WeeklyDigest, WeeklyDigest.player_id == player_id),
        (UsageCounter, UsageCounter.player_id == player_id),
        (TrainingPlan, TrainingPlan.player_id == player_id),
        (TrainingCompletion, TrainingCompletion.player_id == player_id),
        (TrainingDecision, TrainingDecision.player_id == player_id),
        (Repertoire, Repertoire.player_id == player_id),
        (BanditWeights, BanditWeights.player_id == player_id),
        (BanditExperience, BanditExperience.player_id == player_id),
        (FeedbackMessage, FeedbackMessage.player_id == player_id),
        (ContentReport, ContentReport.player_id == player_id),
        (LinkedAccount, LinkedAccount.player_id == player_id),
        (LichessImportJob, LichessImportJob.player_id == player_id),
        (Notification, Notification.player_id == player_id),
        (Game, Game.player_id == player_id),
        (GameEvent, GameEvent.player_id == player_id),
        (Session, Session.player_id == player_id),
        # The account row itself — always last.
        (Player, Player.id == player_id),
    ]


#: Every table the erasure plan touches, in deletion order.  Pinned by
#: test_auth_account_deletion.py against the metadata-driven discovery
#: in ``player_linked_tables`` so the plan can never silently fall
#: behind the schema.
ERASED_TABLES: tuple[str, ...] = tuple(
    model.__tablename__ for model, _ in _erasure_plan("__erased-tables-probe__")
)


def player_data_plan(player_id: str) -> list[tuple[type[Base], Any]]:
    """The ``(model, criterion)`` scope shared by BOTH data-subject-rights
    consumers: ``purge_player_data`` (Art. 17) deletes it and
    ``export.export_player_data`` (Art. 15/20) serialises it.  One
    authority — so the metadata-discovery tripwire that keeps erasure
    complete keeps the export complete in the same breath.
    """
    return _erasure_plan(player_id)


def player_linked_tables(metadata: MetaData) -> frozenset[str]:
    """Discover every table that can hold rows belonging to a player.

    A table is player-linked when it has a ``player_id`` column (with or
    without an FK constraint — three tables carry the bare column), or
    when any of its FKs points at a table already in the linked set
    (transitive closure, so ``training_outcomes`` is found through
    ``training_decisions`` even though it never names the player).

    ``players`` itself is excluded from the result: callers treat the
    account row separately from its dependents.
    """
    linked: set[str] = {Player.__tablename__}
    grew = True
    while grew:
        grew = False
        for table in metadata.tables.values():
            if table.name in linked:
                continue
            has_player_column = any(column.name == "player_id" for column in table.columns)
            references_linked = any(
                fk.column.table.name in linked
                for column in table.columns
                for fk in column.foreign_keys
            )
            if has_player_column or references_linked:
                linked.add(table.name)
                grew = True
    return frozenset(linked - {Player.__tablename__})


def purge_player_data(db: DBSession, player_id: str) -> dict[str, int]:
    """Delete every row belonging to ``player_id``, then the player row.

    Returns ``{table_name: rows_deleted}``.  Commits on success; on an
    exception mid-plan nothing is committed, so the caller's session
    teardown discards the partial work and the account stays intact.

    Bulk ``query(...).delete(synchronize_session=False)`` is used for
    every step so the deletion set is exactly what ``_erasure_plan``
    states — no ORM relationship cascade can widen or narrow it.
    """
    counts: dict[str, int] = {}
    for model, criterion in _erasure_plan(player_id):
        counts[model.__tablename__] = int(
            db.query(model).filter(criterion).delete(synchronize_session=False)
        )
    db.commit()
    logger.info(
        "erasure: player_id=%s purged rows=%d across tables=%d",
        player_id,
        sum(counts.values()),
        sum(1 for deleted in counts.values() if deleted),
    )
    return counts
