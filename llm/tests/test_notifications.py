"""Tests for the in-app notification feed (module + HTTP surface).

Covers ``llm.seca.notifications.service`` (feed visibility, badge
semantics, lifecycle mutations, the two v1 producers with their
batching / dedup rules) and ``llm.seca.notifications.router``
(ownership, error translation, response shapes).  Router-layer tests
call handlers directly with a stub request — the same pattern as
``test_lichess_import.py`` — so no live app is needed.  The
import-service integration (who CALLS these producers and when) is
pinned separately in ``test_lichess_import.py`` DC_01..DC_08.

Pinned invariants
-----------------
NS_01  get_feed hides dismissed, expired, and >30-day rows; newest first.
NS_02  Badge counts only medium+ unread visible rows (spec §5.6).
NS_03  mark_read is idempotent — the first read_at survives a re-read.
NS_04  mark_all_read marks exactly the visible unread rows.
NS_05  dismiss removes the row from the feed; row survives in the DB.
NS_06  notify_games_analyzed: single-game copy at count 1, batched at N.
NS_07  §5.4 batching: a second import inside 60 min merges into the
       unread row (summed count, created_at reset, still one row).
NS_08  §5.4 edge: a READ row is never merged into — fresh row instead.
NS_09  An unread row older than the window is not merged into.
NS_10  notify_games_analyzed with count <= 0 creates nothing.
NS_11  notify_lichess_disconnected: high priority, no expiry, reconnect
       action, username in body.
NS_12  Duplicate suppression: one live alert regardless of retries, and
       a READ (but undismissed) alert is not re-created or un-read.
NS_13  resolve_lichess_disconnected dismisses live alerts only (other
       types untouched), returns the dismissed count.

NR_01  GET /notifications returns serialized rows + unread_count, and
       the wire shape carries exactly the documented keys.
NR_02  Mutations 404 on unknown id.
NR_03  Mutations 403 on another player's row (probe-visible).
NR_04  Oversized notification_id → 400 before any DB lookup.
NR_05  POST read marks read and returns the fresh badge count.
NR_06  POST read-all returns marked count and unread_count 0.
NR_07  POST dismiss removes the row from a subsequent feed read.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request as StarletteRequest

# Import all model modules so Base.metadata sees every table.
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.lichess.models  # noqa: F401
import llm.seca.notifications.models  # noqa: F401

from llm.seca.auth.models import Base, Player
from llm.seca.notifications import service
from llm.seca.notifications.models import (
    ACTION_LICHESS_RECONNECT,
    ACTION_OPEN_HISTORY,
    Notification,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    TYPE_GAME_ANALYZED,
    TYPE_SYSTEM_ALERT,
)
from llm.seca.notifications.router import dismiss_one, feed, read_all, read_one
from llm.seca.shared_limiter import limiter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def player(db_session):
    p = Player(
        email="notif@test.com",
        password_hash="dummy",
        rating=1200.0,
        confidence=0.5,
        skill_vector_json="{}",
        player_embedding="[]",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture()
def other_player(db_session):
    p = Player(
        email="notif-other@test.com",
        password_hash="dummy",
        rating=1200.0,
        confidence=0.5,
        skill_vector_json="{}",
        player_embedding="[]",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _fake_request() -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/notifications",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
    )


@contextmanager
def _limiter_disabled():
    prev = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = prev


def _add_notification(db_session, player, **overrides) -> Notification:
    """Insert a visible medium-priority row, letting tests override fields."""
    fields = {
        "player_id": player.id,
        "type": TYPE_GAME_ANALYZED,
        "priority": PRIORITY_MEDIUM,
        "title": "Review ready",
        "body": "A game from your Lichess history is ready to explore.",
        "action": ACTION_OPEN_HISTORY,
        "action_label": "See games",
    }
    fields.update(overrides)
    row = Notification(**fields)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Service — feed visibility + badge
# ---------------------------------------------------------------------------


class TestFeedVisibility:
    # NS_01
    def test_feed_hides_dismissed_expired_and_stale_rows(self, db_session, player):
        now = datetime.utcnow()
        visible = _add_notification(db_session, player)
        _add_notification(db_session, player, dismissed_at=now)
        _add_notification(db_session, player, expires_at=now - timedelta(minutes=1))
        _add_notification(db_session, player, created_at=now - timedelta(days=31))

        result = service.get_feed(db_session, player.id)
        assert [n["id"] for n in result["notifications"]] == [visible.id]

    # NS_01 (ordering)
    def test_feed_is_newest_first(self, db_session, player):
        now = datetime.utcnow()
        older = _add_notification(db_session, player, created_at=now - timedelta(hours=2))
        newer = _add_notification(db_session, player, created_at=now - timedelta(hours=1))

        result = service.get_feed(db_session, player.id)
        assert [n["id"] for n in result["notifications"]] == [newer.id, older.id]

    # NS_01 (isolation)
    def test_feed_is_player_scoped(self, db_session, player, other_player):
        _add_notification(db_session, other_player)
        assert service.get_feed(db_session, player.id)["notifications"] == []

    # NS_02
    def test_badge_counts_medium_plus_unread_only(self, db_session, player):
        _add_notification(db_session, player)  # medium unread → counts
        _add_notification(db_session, player, priority=PRIORITY_HIGH)  # counts
        _add_notification(db_session, player, priority=PRIORITY_LOW)  # low → no
        _add_notification(db_session, player, read_at=datetime.utcnow())  # read → no
        _add_notification(db_session, player, dismissed_at=datetime.utcnow())  # dismissed → no

        assert service.unread_badge_count(db_session, player.id) == 2
        # Low-priority and already-read rows still APPEAR in the feed —
        # the badge is the only surface that filters them out.
        assert len(service.get_feed(db_session, player.id)["notifications"]) == 4


# ---------------------------------------------------------------------------
# Service — lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    # NS_03
    def test_mark_read_is_idempotent(self, db_session, player):
        row = _add_notification(db_session, player)
        service.mark_read(db_session, row)
        first = row.read_at
        assert first is not None
        service.mark_read(db_session, row)
        assert row.read_at == first

    # NS_04
    def test_mark_all_read_touches_only_visible_unread(self, db_session, player):
        _add_notification(db_session, player)
        _add_notification(db_session, player, priority=PRIORITY_HIGH)
        already_read = _add_notification(db_session, player, read_at=datetime.utcnow())
        dismissed = _add_notification(db_session, player, dismissed_at=datetime.utcnow())

        assert service.mark_all_read(db_session, player.id) == 2
        assert service.unread_badge_count(db_session, player.id) == 0
        db_session.refresh(dismissed)
        assert dismissed.read_at is None  # not visible → untouched
        db_session.refresh(already_read)
        assert already_read.read_at is not None

    # NS_05
    def test_dismiss_soft_deletes(self, db_session, player):
        row = _add_notification(db_session, player)
        service.dismiss(db_session, row)
        assert service.get_feed(db_session, player.id)["notifications"] == []
        # Row survives in the DB for analytics.
        assert db_session.query(Notification).filter_by(id=row.id).count() == 1


# ---------------------------------------------------------------------------
# Service — game_analyzed producer (spec §5.4 batching)
# ---------------------------------------------------------------------------


class TestGamesAnalyzedProducer:
    # NS_06
    def test_single_and_batched_copy(self, db_session, player):
        service.notify_games_analyzed(db_session, player.id, 1)
        row = db_session.query(Notification).one()
        assert row.title == "Review ready"
        assert row.type == TYPE_GAME_ANALYZED
        assert row.action == ACTION_OPEN_HISTORY
        assert row.expires_at is not None
        assert json.loads(row.metadata_json) == {"games_analyzed": 1}

    # NS_07
    def test_second_import_within_window_merges(self, db_session, player):
        service.notify_games_analyzed(db_session, player.id, 1)
        first = db_session.query(Notification).one()
        first_created = first.created_at

        service.notify_games_analyzed(db_session, player.id, 2)
        rows = db_session.query(Notification).all()
        assert len(rows) == 1
        merged = rows[0]
        assert merged.id == first.id
        assert merged.title == "3 games reviewed"
        assert json.loads(merged.metadata_json) == {"games_analyzed": 3}
        assert merged.created_at >= first_created  # reset to now

    # NS_08
    def test_read_row_is_not_merged_into(self, db_session, player):
        service.notify_games_analyzed(db_session, player.id, 1)
        row = db_session.query(Notification).one()
        service.mark_read(db_session, row)

        service.notify_games_analyzed(db_session, player.id, 1)
        rows = db_session.query(Notification).order_by(Notification.created_at).all()
        assert len(rows) == 2
        assert rows[0].read_at is not None
        assert rows[1].read_at is None

    # NS_09
    def test_stale_unread_row_is_not_merged_into(self, db_session, player):
        service.notify_games_analyzed(db_session, player.id, 1)
        row = db_session.query(Notification).one()
        row.created_at = datetime.utcnow() - timedelta(
            minutes=service.GAME_ANALYZED_MERGE_WINDOW_MINUTES + 1
        )
        db_session.commit()

        service.notify_games_analyzed(db_session, player.id, 1)
        assert db_session.query(Notification).count() == 2

    # NS_10
    def test_non_positive_count_is_a_noop(self, db_session, player):
        service.notify_games_analyzed(db_session, player.id, 0)
        service.notify_games_analyzed(db_session, player.id, -3)
        assert db_session.query(Notification).count() == 0


# ---------------------------------------------------------------------------
# Service — lichess_disconnected producer + resolution
# ---------------------------------------------------------------------------


class TestDisconnectedAlertProducer:
    # NS_11
    def test_alert_shape(self, db_session, player):
        service.notify_lichess_disconnected(db_session, player.id, "alice")
        row = db_session.query(Notification).one()
        assert row.type == TYPE_SYSTEM_ALERT
        assert row.priority == PRIORITY_HIGH
        assert row.action == ACTION_LICHESS_RECONNECT
        assert row.expires_at is None
        assert "alice" in row.body

    # NS_12
    def test_duplicate_suppression_preserves_read_state(self, db_session, player):
        service.notify_lichess_disconnected(db_session, player.id, "alice")
        row = db_session.query(Notification).one()
        service.mark_read(db_session, row)
        read_stamp = row.read_at

        service.notify_lichess_disconnected(db_session, player.id, "alice")
        rows = db_session.query(Notification).all()
        assert len(rows) == 1
        assert rows[0].read_at == read_stamp

    # NS_13
    def test_resolve_dismisses_only_live_alerts(self, db_session, player, other_player):
        service.notify_lichess_disconnected(db_session, player.id, "alice")
        service.notify_lichess_disconnected(db_session, other_player.id, "bob")
        unrelated = _add_notification(db_session, player)

        assert service.resolve_lichess_disconnected(db_session, player.id) == 1
        # Player's alert dismissed; the other player's and the unrelated
        # row untouched.
        live = db_session.query(Notification).filter(Notification.dismissed_at.is_(None)).all()
        assert {n.id for n in live} == {
            unrelated.id,
            db_session.query(Notification)
            .filter(Notification.player_id == other_player.id)
            .one()
            .id,
        }
        # Second resolve is a no-op.
        assert service.resolve_lichess_disconnected(db_session, player.id) == 0


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class TestRouter:
    # NR_01
    def test_feed_shape(self, db_session, player):
        _add_notification(db_session, player)
        with _limiter_disabled():
            result = feed(_fake_request(), player=player, db=db_session)
        assert result["unread_count"] == 1
        (entry,) = result["notifications"]
        assert set(entry.keys()) == {
            "id",
            "type",
            "priority",
            "title",
            "body",
            "action",
            "action_label",
            "metadata",
            "created_at",
            "read_at",
        }
        assert entry["read_at"] is None
        assert isinstance(entry["metadata"], dict)

    # NR_02
    def test_unknown_id_404(self, db_session, player):
        with _limiter_disabled(), pytest.raises(HTTPException) as exc:
            read_one(_fake_request(), "no-such-id", player=player, db=db_session)
        assert exc.value.status_code == 404

    # NR_03
    def test_cross_player_403(self, db_session, player, other_player):
        row = _add_notification(db_session, other_player)
        with _limiter_disabled(), pytest.raises(HTTPException) as exc:
            read_one(_fake_request(), row.id, player=player, db=db_session)
        assert exc.value.status_code == 403
        with _limiter_disabled(), pytest.raises(HTTPException) as exc:
            dismiss_one(_fake_request(), row.id, player=player, db=db_session)
        assert exc.value.status_code == 403

    # NR_04
    def test_oversized_id_400(self, db_session, player):
        with _limiter_disabled(), pytest.raises(HTTPException) as exc:
            read_one(_fake_request(), "x" * 65, player=player, db=db_session)
        assert exc.value.status_code == 400
        assert exc.value.detail == "notification_id too long"

    # NR_05
    def test_read_one_marks_and_returns_badge(self, db_session, player):
        row = _add_notification(db_session, player)
        _add_notification(db_session, player)
        with _limiter_disabled():
            result = read_one(_fake_request(), row.id, player=player, db=db_session)
        assert result == {"read": True, "unread_count": 1}
        db_session.refresh(row)
        assert row.read_at is not None

    # NR_06
    def test_read_all(self, db_session, player):
        _add_notification(db_session, player)
        _add_notification(db_session, player)
        with _limiter_disabled():
            result = read_all(_fake_request(), player=player, db=db_session)
        assert result == {"marked": 2, "unread_count": 0}

    # NR_07
    def test_dismiss_removes_from_feed(self, db_session, player):
        row = _add_notification(db_session, player)
        with _limiter_disabled():
            result = dismiss_one(_fake_request(), row.id, player=player, db=db_session)
        assert result == {"dismissed": True, "unread_count": 0}
        with _limiter_disabled():
            assert feed(_fake_request(), player=player, db=db_session)["notifications"] == []
