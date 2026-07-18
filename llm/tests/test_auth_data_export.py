"""
Backend tests for GET /auth/me/export — GDPR Art. 15/20 data export.

The export's table scope is structurally tied to the erasure plan
(both read ``erasure.player_data_plan``), so the deletion suite's
metadata-discovery tripwire already guarantees coverage of new
player-linked tables.  This file pins the READ side: scope parity,
secrets policy, cross-player isolation, and end-to-end
JSON-serialisability.  The seed helper is deliberately duplicated from
test_auth_account_deletion.py (no shared test package exists) and is
self-guarding: EX_02 fails if it ever covers less than the discovery.

Pinned invariants
-----------------
 1. EX_SCOPE_MATCHES_ERASURE   export ``data`` keys == ERASED_TABLES
                               (players included, nothing missing,
                               nothing extra, empty tables present
                               as []).
 2. EX_SEED_COVERS_DISCOVERY   the seed populates every discovered
                               player-linked table (self-guard for the
                               duplicated helper).
 3. EX_EXPORTS_SEEDED_ROWS     every seeded table exports exactly the
                               victim's one row; content round-trips
                               verbatim (chat text, PGN, email).
 4. EX_BYSTANDER_ISOLATION     a second player's rows never appear in
                               the victim's export.
 5. EX_NO_SECRETS              password_hash / token_hash /
                               previous_token_hash keys appear nowhere
                               in the document.
 6. EX_SECRET_PATTERN_GUARD    any column in the player-linked closure
                               whose name contains password/token/secret
                               must be in COLUMN_EXCLUSIONS — a future
                               credential column cannot leak by default.
 7. EX_JSON_SERIALISABLE       json.dumps of the full document succeeds
                               (datetimes ISO-8601, JSON columns nested).
 8. EX_EMPTY_ACCOUNT_SHAPE     a fresh player exports the players row +
                               [] for every other table.
 9. EX_ENDPOINT_SHAPE          the route handler returns the document
                               (export_version / player_id / data).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from llm.seca.auth.erasure import ERASED_TABLES, player_linked_tables
from llm.seca.auth.export import COLUMN_EXCLUSIONS, export_player_data
from llm.seca.auth.models import Base, Player, Session
from llm.seca.auth.router import export_me
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

_SECRET_NAME = re.compile(r"password|token|secret", re.IGNORECASE)


def _fake_request() -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "GET",
            "path": "/auth/me/export",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
    )


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
    """One player + exactly one row in EVERY player-linked table."""
    pid = f"player-{tag}"
    eid = f"event-{tag}"
    gid = f"game-{tag}"
    plid = f"plan-{tag}"
    did = f"decision-{tag}"

    db.add(Player(id=pid, email=f"{tag}@export.test", password_hash="super-secret-hash"))
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
    db.add(AnalyticsEvent(player_id=pid, event_type="game_finished", payload={"result": "win"}))
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


class TestExportScope:
    def test_scope_matches_erasure(self, db_session):
        """EX_SCOPE_MATCHES_ERASURE: the export's table keys are exactly
        the erasure plan's — empty tables present as [], never omitted."""
        pid = _seed_player_universe(db_session, "victim")
        doc = export_player_data(db_session, pid)
        assert set(doc["data"]) == set(ERASED_TABLES)
        for table, rows in doc["data"].items():
            assert isinstance(rows, list), f"{table} must map to a list"

    def test_seed_covers_discovery(self):
        """EX_SEED_COVERS_DISCOVERY: this file's duplicated seed helper
        stays honest — it must populate every discovered table."""
        discovered = player_linked_tables(Base.metadata)
        assert _SEEDED_TABLES == discovered, (
            f"Seed drift: missing={sorted(discovered - _SEEDED_TABLES)} "
            f"extra={sorted(_SEEDED_TABLES - discovered)} — update "
            "_seed_player_universe here AND in test_auth_account_deletion.py."
        )


class TestExportContent:
    def test_exports_seeded_rows_verbatim(self, db_session):
        """EX_EXPORTS_SEEDED_ROWS: one victim row per table, with content
        round-tripping verbatim."""
        pid = _seed_player_universe(db_session, "victim")
        doc = export_player_data(db_session, pid)
        data = doc["data"]

        for table in _SEEDED_TABLES:
            assert len(data[table]) == 1, f"expected 1 exported row in {table}"
        assert len(data["players"]) == 1

        assert data["players"][0]["email"] == "victim@export.test"
        assert data["chat_turns"][0]["content"] == "hello coach"
        assert data["game_events"][0]["pgn"] == "1. e4 e5"
        assert data["feedback_messages"][0]["message"] == "great app"
        assert data["notifications"][0]["title"] == "Reconnect Lichess"
        assert data["analytics_events"][0]["payload"] == {"result": "win"}

    def test_bystander_rows_never_exported(self, db_session):
        """EX_BYSTANDER_ISOLATION."""
        victim = _seed_player_universe(db_session, "victim")
        _seed_player_universe(db_session, "bystander")

        doc = export_player_data(db_session, victim)
        data = doc["data"]

        for table in _SEEDED_TABLES | {"players"}:
            assert len(data[table]) == 1, f"{table} leaked a bystander row"
        assert data["players"][0]["email"] == "victim@export.test"
        for row in data["chat_turns"]:
            assert row["player_id"] == victim

    def test_empty_account_shape(self, db_session):
        """EX_EMPTY_ACCOUNT_SHAPE: fresh player → players row + [] for
        every other table."""
        db_session.add(Player(id="p-empty", email="empty@export.test", password_hash="x"))
        db_session.commit()

        doc = export_player_data(db_session, "p-empty")
        data = doc["data"]

        assert len(data["players"]) == 1
        for table in set(ERASED_TABLES) - {"players"}:
            assert data[table] == [], f"{table} must be [] for a fresh account"


class TestSecretsPolicy:
    def test_no_secret_keys_in_document(self, db_session):
        """EX_NO_SECRETS: credential columns never reach the document."""
        pid = _seed_player_universe(db_session, "victim")
        doc = export_player_data(db_session, pid)

        assert "password_hash" not in doc["data"]["players"][0]
        session_row = doc["data"]["sessions"][0]
        assert "token_hash" not in session_row
        assert "previous_token_hash" not in session_row
        blob = json.dumps(doc)
        assert "super-secret-hash" not in blob

    def test_secret_pattern_guard(self):
        """EX_SECRET_PATTERN_GUARD: any secret-smelling column in the
        player-linked closure must be explicitly in COLUMN_EXCLUSIONS."""
        closure = player_linked_tables(Base.metadata) | {"players"}
        leaks = [
            f"{table.name}.{column.name}"
            for table in Base.metadata.tables.values()
            if table.name in closure
            for column in table.columns
            if _SECRET_NAME.search(column.name)
            and column.name not in COLUMN_EXCLUSIONS.get(table.name, frozenset())
        ]
        assert not leaks, (
            f"Secret-smelling columns not covered by COLUMN_EXCLUSIONS: {leaks}. "
            "Add each to llm/seca/auth/export.py::COLUMN_EXCLUSIONS (or rename "
            "the column if it is not actually a credential)."
        )


class TestSerialisation:
    def test_document_is_json_serialisable(self, db_session):
        """EX_JSON_SERIALISABLE: json.dumps succeeds end-to-end and
        datetimes are ISO-8601 strings."""
        pid = _seed_player_universe(db_session, "victim")
        doc = export_player_data(db_session, pid)

        blob = json.dumps(doc)
        assert blob
        created = doc["data"]["players"][0]["created_at"]
        assert isinstance(created, str)
        datetime.fromisoformat(created)


class TestExportEndpoint:
    def test_endpoint_returns_document(self, db_session):
        """EX_ENDPOINT_SHAPE: the route handler returns the export
        document for the authenticated player."""
        pid = _seed_player_universe(db_session, "victim")
        player = db_session.query(Player).filter_by(id=pid).one()

        doc = export_me(request=_fake_request(), player=player, db=db_session)

        assert doc["export_version"] == 1
        assert doc["player_id"] == pid
        assert set(doc["data"]) == set(ERASED_TABLES)
