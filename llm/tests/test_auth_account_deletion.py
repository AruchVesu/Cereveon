"""
Backend tests for DELETE /auth/me — account erasure (GDPR Art. 17).

The erasure plan in llm/seca/auth/erasure.py is an explicit ordered
multi-table delete.  These tests keep it honest with a metadata-driven
discovery of every player-linked table, so a future model added without
registering it in the plan fails CI here — never silently orphans rows
in production.

Pinned invariants
-----------------
 1. AD_DISCOVERY_MATCHES_PLAN    every discovered player-linked table is
                                 in ERASED_TABLES (drift tripwire).
 2. AD_PLAN_HAS_NO_STRAYS        ERASED_TABLES contains nothing outside
                                 discovery + the players row (last).
 3. AD_FIXTURES_COVER_ALL        the seed helper populates every
                                 discovered table (fixture drift tripwire).
 4. AD_PURGE_LEAVES_ZERO_ROWS    after purge, the victim has zero rows in
                                 every player-linked table + no players row.
 5. AD_PURGE_PRESERVES_BYSTANDER a second player's rows all survive.
 6. AD_PURGE_RETURNS_COUNTS      per-table deleted counts are reported.
 7. AD_ENDPOINT_DELETES          DELETE /auth/me handler erases and
                                 returns {"status": "deleted"}.
 8. AD_OLD_TOKEN_401S            the token that authorised the deletion
                                 is dead afterwards (session erased).
 9. AD_LICHESS_ACCOUNT_DELETABLE a Lichess sign-in account (no usable
                                 password) erases the same way.
10. AD_MODEL_FKS_CARRY_CASCADE   every FK edge into the erasure closure
                                 declares ondelete="CASCADE" in metadata.
11. AD_RETROFIT_NOOP_ON_SQLITE   _ensure_fk_delete_cascade never touches
                                 a SQLite connection.
12. AD_RETROFIT_SQL_SHAPE        the Postgres retrofit DDL builder emits
                                 the DROP/ADD pair with ON DELETE CASCADE.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy import text as sa_text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

# erasure imports every model module, so create_all below sees the full
# schema without a wildcard import block of our own.
from llm.seca.auth import erasure
from llm.seca.auth.erasure import ERASED_TABLES, player_linked_tables, purge_player_data
from llm.seca.auth.models import Base, Player, Session
from llm.seca.auth.router import (
    _ensure_fk_delete_cascade,
    _fk_cascade_retrofit_sql,
    delete_me,
    get_current_player,
)
from llm.seca.auth.tokens import create_access_token
from llm.seca.shared_limiter import limiter

from llm.seca.analytics.models import AnalyticsEvent, WeeklyDigest
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


def _fake_request(method: str = "DELETE") -> StarletteRequest:
    """Minimum Request satisfying slowapi's isinstance check and the
    handler's ``request.state`` access."""
    return StarletteRequest(
        {
            "type": "http",
            "method": method,
            "path": "/auth/me",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """In-memory SQLite session with the FULL schema; fresh per test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _limiter_disabled():
    prev_enabled = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = prev_enabled


_NOW = datetime.utcnow()


def _seed_player_universe(db, tag: str) -> str:
    """Create one player + exactly one row in EVERY player-linked table.

    ``tag`` keeps two seeded players' unique-constrained values apart
    (emails, lichess ids, linked-account usernames, ...).  Returns the
    player id.
    """
    pid = f"player-{tag}"
    eid = f"event-{tag}"
    gid = f"game-{tag}"
    plid = f"plan-{tag}"
    did = f"decision-{tag}"

    db.add(Player(id=pid, email=f"{tag}@erasure.test", password_hash="x"))
    db.add(
        Session(
            player_id=pid,
            token_hash=hashlib.sha256(f"tok-{tag}".encode()).hexdigest(),
            expires_at=_NOW + timedelta(days=1),
        )
    )
    db.add(GameEvent(id=eid, player_id=pid, pgn="1. e4 e5", result="win"))
    db.add(GameFinishResult(event_id=eid, response_json="{}"))
    db.add(RatingUpdate(event_id=eid, rating_before=1200.0, rating_after=1210.0))
    db.add(ConfidenceUpdate(event_id=eid, confidence_before=0.5, confidence_after=0.6))
    db.add(GameReview(game_event_id=eid, player_id=pid))
    db.add(MistakeStudyPlan(id=plid, player_id=pid, source_event_id=eid))
    db.add(
        MistakeStudyPuzzle(
            plan_id=plid,
            day_offset=0,
            fen="8/8/8/8/8/8/8/K6k w - - 0 1",
            expected_move_uci="a1a2",
            due_at=_NOW,
        )
    )
    db.add(Game(id=gid, player_id=pid))
    db.add(Move(game_id=gid))
    db.add(Explanation(game_id=gid))
    db.add(ChatTurn(player_id=pid, role="user", content="hello coach"))
    db.add(AnalyticsEvent(player_id=pid, event_type="game_finished", payload={}))
    db.add(
        WeeklyDigest(
            player_id=pid,
            period_start=_NOW - timedelta(days=7),
            period_end=_NOW,
            games_analyzed=1,
            holes=[],
            tasks=[],
        )
    )
    db.add(UsageCounter(player_id=pid, metric="chat_turn", period_key="2026-07-17"))
    db.add(TrainingPlan(player_id=pid, topic="forks", difficulty="easy", exercise_type="puzzle"))
    db.add(
        TrainingCompletion(
            player_id=pid,
            source_type="standard_puzzle",
            source_ref=f"ref-{tag}",
            xp_awarded=10,
        )
    )
    db.add(
        TrainingDecision(
            id=did,
            player_id=pid,
            created_at=_NOW,
            rating_before=1200.0,
            confidence_before=0.5,
            strategy="baseline",
        )
    )
    db.add(
        TrainingOutcome(
            id=f"outcome-{tag}",
            decision_id=did,
            measured_at=_NOW,
            rating_after=1210.0,
            confidence_after=0.6,
            rating_delta=10.0,
            confidence_delta=0.1,
        )
    )
    db.add(Repertoire(player_id=pid, eco="B20", name="Sicilian", line="1. e4 c5"))
    db.add(BanditWeights(player_id=pid, action="hint", n_features=1, A_json="[[1]]", b_json="[0]"))
    db.add(BanditExperience(player_id=pid, context_json="{}", action="hint", reward=0.0))
    db.add(FeedbackMessage(player_id=pid, message="great app"))
    db.add(ContentReport(player_id=pid, content="an offensive coach reply", surface="chat"))
    db.add(LinkedAccount(player_id=pid, platform="lichess", external_username=f"lich-{tag}"))
    db.add(LichessImportJob(player_id=pid, target_max_games=10))
    db.add(
        Notification(
            player_id=pid,
            type="system_alert",
            title="Reconnect Lichess",
            body="We lost access to your linked account.",
        )
    )
    db.commit()
    return pid


#: Tables _seed_player_universe writes to, kept in lock-step with the
#: discovery so a new player-linked model also fails HERE until the
#: seed helper covers it (which keeps AD_PURGE_* meaningful).
_SEEDED_TABLES: frozenset[str] = frozenset(
    {
        "sessions",
        "game_events",
        "game_finish_results",
        "rating_updates",
        "confidence_updates",
        "game_reviews",
        "mistake_study_plans",
        "mistake_study_puzzles",
        "games",
        "moves",
        "explanations",
        "chat_turns",
        "analytics_events",
        "weekly_digests",
        "usage_counters",
        "training_plans",
        "training_completions",
        "training_decisions",
        "training_outcomes",
        "repertoire",
        "bandit_weights",
        "bandit_experiences",
        "feedback_messages",
        "content_reports",
        "linked_accounts",
        "lichess_import_jobs",
        "notifications",
    }
)


def _table_count(db, table: str) -> int:
    return int(db.execute(sa_text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())


# ---------------------------------------------------------------------------
# 1.  Plan completeness (metadata-driven drift tripwires)
# ---------------------------------------------------------------------------


class TestErasurePlanCompleteness:
    def test_discovery_matches_plan(self):
        """AD_DISCOVERY_MATCHES_PLAN: every player-linked table found in
        the live metadata must be handled by the erasure plan."""
        discovered = player_linked_tables(Base.metadata)
        missing = discovered - set(ERASED_TABLES)
        assert not missing, (
            f"Player-linked tables missing from the erasure plan: {sorted(missing)}. "
            "Register them in llm/seca/auth/erasure.py::_erasure_plan (children "
            "before parents) AND seed them in _seed_player_universe here."
        )

    def test_plan_has_no_strays(self):
        """AD_PLAN_HAS_NO_STRAYS: the plan deletes only player-linked
        tables plus the players row, which must come last."""
        discovered = player_linked_tables(Base.metadata)
        strays = set(ERASED_TABLES) - discovered - {"players"}
        assert not strays, f"Erasure plan touches non-player-linked tables: {sorted(strays)}"
        assert ERASED_TABLES[-1] == "players", "players row must be deleted last"
        assert ERASED_TABLES.count("players") == 1

    def test_fixtures_cover_all_discovered_tables(self):
        """AD_FIXTURES_COVER_ALL: the seed helper populates every
        discovered table, so the behavioural tests below exercise the
        full closure."""
        discovered = player_linked_tables(Base.metadata)
        unseeded = discovered - _SEEDED_TABLES
        assert not unseeded, (
            f"_seed_player_universe does not populate: {sorted(unseeded)}. "
            "Add a row factory for each so AD_PURGE_LEAVES_ZERO_ROWS stays honest."
        )
        assert _SEEDED_TABLES == discovered


# ---------------------------------------------------------------------------
# 2.  Purge behaviour (two players, full universe each)
# ---------------------------------------------------------------------------


class TestPurgePlayerData:
    def test_purge_leaves_zero_rows_and_preserves_bystander(self, db_session):
        """AD_PURGE_LEAVES_ZERO_ROWS + AD_PURGE_PRESERVES_BYSTANDER +
        AD_PURGE_RETURNS_COUNTS."""
        victim = _seed_player_universe(db_session, "victim")
        bystander = _seed_player_universe(db_session, "bystander")

        for table in _SEEDED_TABLES:
            assert _table_count(db_session, table) == 2, f"seed failed for {table}"
        assert _table_count(db_session, "players") == 2

        counts = purge_player_data(db_session, victim)

        # AD_PURGE_RETURNS_COUNTS — every planned table reported; each
        # seeded table lost exactly the victim's one row.
        assert set(counts) == set(ERASED_TABLES)
        for table in _SEEDED_TABLES:
            assert counts[table] == 1, f"expected 1 deleted row in {table}, got {counts[table]}"
        assert counts["players"] == 1

        # AD_PURGE_LEAVES_ZERO_ROWS / AD_PURGE_PRESERVES_BYSTANDER —
        # exactly the bystander's row remains everywhere.
        for table in _SEEDED_TABLES:
            assert _table_count(db_session, table) == 1, f"{table} not reduced to bystander row"
        assert _table_count(db_session, "players") == 1

        assert db_session.query(Player).filter_by(id=victim).first() is None
        survivor = db_session.query(Player).filter_by(id=bystander).first()
        assert survivor is not None and survivor.email == "bystander@erasure.test"
        for row in db_session.query(ChatTurn).all():
            assert row.player_id == bystander
        for row in db_session.query(GameEvent).all():
            assert row.player_id == bystander

    def test_purge_is_idempotent_for_missing_player(self, db_session):
        """Purging an id with no rows deletes nothing and does not raise."""
        counts = purge_player_data(db_session, "no-such-player")
        assert sum(counts.values()) == 0


# ---------------------------------------------------------------------------
# 3.  Endpoint behaviour
# ---------------------------------------------------------------------------


class TestDeleteMeEndpoint:
    def test_endpoint_deletes_and_old_token_401s(self, db_session):
        """AD_ENDPOINT_DELETES + AD_OLD_TOKEN_401S."""
        pid = _seed_player_universe(db_session, "victim")
        session_id = str(uuid.uuid4())
        token = create_access_token(player_id=pid, session_id=session_id)
        db_session.add(
            Session(
                id=session_id,
                player_id=pid,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=_NOW + timedelta(days=1),
            )
        )
        db_session.commit()
        player = db_session.query(Player).filter_by(id=pid).one()

        body = delete_me(request=_fake_request(), player=player, db=db_session)
        assert body == {"status": "deleted"}
        assert db_session.query(Player).filter_by(id=pid).first() is None
        assert db_session.query(Session).filter_by(player_id=pid).count() == 0

        with pytest.raises(HTTPException) as excinfo:
            get_current_player(
                request=_fake_request("GET"),
                response=Response(),
                authorization=f"Bearer {token}",
                db=db_session,
            )
        assert excinfo.value.status_code == 401

    def test_lichess_account_deletes_without_password(self, db_session):
        """AD_LICHESS_ACCOUNT_DELETABLE: synthetic-email accounts (no
        usable password) erase identically — the bearer token is the
        proof of identity."""
        pid = _seed_player_universe(db_session, "lichessuser")
        player = db_session.query(Player).filter_by(id=pid).one()
        player.email = "lichess:lichessuser"
        player.lichess_user_id = "lichessuser"
        db_session.commit()

        body = delete_me(request=_fake_request(), player=player, db=db_session)
        assert body == {"status": "deleted"}
        assert db_session.query(Player).filter_by(id=pid).first() is None
        assert db_session.query(LinkedAccount).filter_by(player_id=pid).count() == 0
        assert db_session.query(GameEvent).filter_by(player_id=pid).count() == 0


# ---------------------------------------------------------------------------
# 4.  Schema-level cascade declarations + Postgres retrofit
# ---------------------------------------------------------------------------


class TestCascadeDeclarations:
    def test_every_fk_into_closure_declares_cascade(self):
        """AD_MODEL_FKS_CARRY_CASCADE: any FK whose target is players or
        another player-linked table must carry ondelete=CASCADE, so
        fresh schemas and the Postgres retrofit stay in lock-step with
        the erasure plan."""
        closure = player_linked_tables(Base.metadata) | {"players"}
        missing = [
            f"{table.name}.{column.name} -> {fk.column.table.name}"
            for table in Base.metadata.tables.values()
            for column in table.columns
            for fk in column.foreign_keys
            if fk.column.table.name in closure and fk.ondelete != "CASCADE"
        ]
        assert not missing, f"FKs missing ondelete=CASCADE: {missing}"

    def test_retrofit_is_noop_on_sqlite(self):
        """AD_RETROFIT_NOOP_ON_SQLITE: under the SQLite test engine the
        retrofit returns before touching the connection."""
        conn = MagicMock()
        assert _ensure_fk_delete_cascade(conn) is None
        conn.execute.assert_not_called()
        conn.commit.assert_not_called()

    def test_retrofit_sql_shape(self):
        """AD_RETROFIT_SQL_SHAPE: the DDL builder emits a DROP + ADD pair
        that re-creates the SAME constraint name with ON DELETE CASCADE
        and quotes every identifier."""
        drop, add = _fk_cascade_retrofit_sql(
            "chat_turns_player_id_fkey", "chat_turns", "player_id", "players", "id"
        )
        assert drop == 'ALTER TABLE "chat_turns" DROP CONSTRAINT "chat_turns_player_id_fkey"'
        assert add == (
            'ALTER TABLE "chat_turns" ADD CONSTRAINT "chat_turns_player_id_fkey" '
            'FOREIGN KEY ("player_id") REFERENCES "players" ("id") ON DELETE CASCADE'
        )


# Silence the "imported but unused" impression for readers: the direct
# model imports above are load-bearing — importing llm.seca.auth.erasure
# registers every table on Base.metadata before create_all runs, and the
# seed helper instantiates each class explicitly.
_ = erasure
