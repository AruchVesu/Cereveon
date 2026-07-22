"""Behaviour tests for the entitlements metering service (Subtask 2).

The service is DORMANT by default (``SECA_ENTITLEMENTS_ENFORCED`` unset
⇒ every primitive allows and writes nothing); with the flag on it
enforces the freemium LIMITS table.  These tests pin:

1.  True dormancy — flag off ⇒ allow + ZERO rows written.
2.  Flag polarity — only "true"/"1"/"yes" enable; typos stay dormant
    (opposite failure posture to SECA_SAFE_MODE, by design).
3.  Free chat quota — 3rd turn allowed, 4th blocked; used/remaining
    populated for the 402 body.
4.  Monthly rollover — import_analysis buckets by "YYYY-MM" via the
    injectable ``now``.
5.  Distinct-game admission — 1st game_id admits (marker written), a
    2nd distinct game_id the same day degrades with NO marker, both
    decisions are idempotent per game, and a missing game_id fails
    OPEN (pre-game_id clients are never punished).
6.  Pro thresholds — same machinery, higher numbers (10/30/50).
7.  set_plan — flips the row, rejects unknown plans, re-raises after
    rollback on commit failure (billing must observe the failure).
8.  Freeze-guard cleanliness — the production FORBIDDEN_PATTERNS from
    llm/seca/safety/freeze.py find nothing in the service source.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from llm.seca.auth.models import Base, Player
from llm.seca.entitlements import service
from llm.seca.entitlements.models import UsageCounter

_JULY_3 = datetime(2026, 7, 3, 12, 0, 0)
_JULY_4 = datetime(2026, 7, 4, 12, 0, 0)
_AUGUST_1 = datetime(2026, 8, 1, 12, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def enforced(monkeypatch):
    monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")


def _make_player(db, email: str = "p@test.com", plan: str | None = None) -> Player:
    kwargs = {} if plan is None else {"plan": plan}
    p = Player(email=email, password_hash="not-used-here", **kwargs)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _rows(db) -> list[UsageCounter]:
    return db.query(UsageCounter).all()


def _seed_counter(db, player, metric: str, period_key: str, count: int, created_at=None) -> None:
    row = UsageCounter(player_id=player.id, metric=metric, period_key=period_key, count=count)
    if created_at is not None:
        # Rolling metrics anchor their window on created_at; let callers pin it.
        row.created_at = created_at
    db.add(row)
    db.commit()


# ---------------------------------------------------------------------------
# 1 + 2. Dormancy and flag polarity
# ---------------------------------------------------------------------------


class TestDormancy:
    def test_flag_off_allows_everything_and_writes_nothing(self, db, monkeypatch):
        monkeypatch.delenv("SECA_ENTITLEMENTS_ENFORCED", raising=False)
        player = _make_player(db)

        for _ in range(10):
            service.record(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)
        chat = service.check(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)
        game = service.admit(db, player, service.METRIC_COACHED_GAME, "game-x", now=_JULY_3)

        assert chat.allowed and not chat.degrade
        assert game.allowed and not game.degrade
        assert chat.limit is None and chat.used is None
        assert _rows(db) == [], "dormant mode must not write usage rows"

    @pytest.mark.parametrize("raw", ["", "0", "false", "no", "TRUE-ish", "on", "enforced"])
    def test_unrecognised_values_stay_dormant(self, monkeypatch, raw):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", raw)
        assert service.resolve_enforced() is False

    @pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", " Yes "])
    def test_enable_values_enforce(self, monkeypatch, raw):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", raw)
        assert service.resolve_enforced() is True


# ---------------------------------------------------------------------------
# 3. Free chat quota (the /chat 402 source)
# ---------------------------------------------------------------------------


class TestFreeChatQuota:
    def test_third_turn_allowed_fourth_blocked(self, db, enforced):
        player = _make_player(db)

        for expected_used in range(3):  # turns 1..3
            decision = service.check(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)
            assert decision.allowed, f"turn {expected_used + 1} should be allowed"
            assert decision.used == expected_used
            service.record(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)

        fourth = service.check(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)
        assert not fourth.allowed
        assert not fourth.degrade, "chat is BLOCK behaviour, never degrade"
        assert (fourth.plan, fourth.limit, fourth.used, fourth.remaining) == ("free", 3, 3, 0)

    def test_quota_resets_next_day(self, db, enforced):
        player = _make_player(db)
        for _ in range(3):
            service.record(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)
        assert not service.check(db, player, service.METRIC_CHAT_TURN, now=_JULY_3).allowed
        assert service.check(db, player, service.METRIC_CHAT_TURN, now=_JULY_4).allowed

    def test_record_increments_single_row(self, db, enforced):
        player = _make_player(db)
        for _ in range(3):
            service.record(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)
        rows = _rows(db)
        assert len(rows) == 1 and rows[0].count == 3 and rows[0].subject == ""


# ---------------------------------------------------------------------------
# 4. Monthly rollover
# ---------------------------------------------------------------------------


class TestMonthlyRollover:
    def test_import_analysis_buckets_by_month(self, db, enforced):
        # import_analysis is a MARKER metric (admitted per game_event_id by
        # the review worker — service._MARKER_METRICS), so meter it the way
        # production does.  This test used record() until the check()/admit()
        # row-shape split was fixed: check() read the never-written
        # subject=="" counter row and always reported used=0.
        player = _make_player(db)
        for i in range(3):
            service.admit(db, player, service.METRIC_IMPORT_ANALYSIS, f"event-{i}", now=_JULY_3)
        july = service.check(db, player, service.METRIC_IMPORT_ANALYSIS, now=_JULY_4)
        assert not july.allowed, "3/month exhausted within July"

        august = service.check(db, player, service.METRIC_IMPORT_ANALYSIS, now=_AUGUST_1)
        assert august.allowed and august.used == 0, "new month, fresh bucket"


# ---------------------------------------------------------------------------
# 4a. Pro daily review smoothing cap (import_analysis_daily, 2026-07-15)
# ---------------------------------------------------------------------------


class TestImportAnalysisDailyCap:
    def test_pro_daily_cap_buckets_by_day(self, db, enforced):
        """10 distinct games admit today; the 11th blocks; tomorrow is a
        fresh bucket.  Marker semantics identical to the monthly metric."""
        player = _make_player(db, plan="pro")
        for i in range(10):
            admitted = service.admit(
                db, player, service.METRIC_IMPORT_ANALYSIS_DAILY, f"ev-{i}", now=_JULY_3
            )
            assert admitted.allowed
        eleventh = service.admit(
            db, player, service.METRIC_IMPORT_ANALYSIS_DAILY, "ev-10", now=_JULY_3
        )
        assert not eleventh.allowed

        tomorrow = service.check(db, player, service.METRIC_IMPORT_ANALYSIS_DAILY, now=_JULY_4)
        assert tomorrow.allowed and tomorrow.used == 0, "new day, fresh bucket"

    def test_pro_daily_readmission_of_same_game_is_free(self, db, enforced):
        """A same-day retry of an already-admitted game must not consume a
        second daily slot — same subject-marker idempotency as monthly."""
        player = _make_player(db, plan="pro")
        for i in range(9):
            service.admit(db, player, service.METRIC_IMPORT_ANALYSIS_DAILY, f"ev-{i}", now=_JULY_3)
        service.admit(db, player, service.METRIC_IMPORT_ANALYSIS_DAILY, "target", now=_JULY_3)
        again = service.admit(
            db, player, service.METRIC_IMPORT_ANALYSIS_DAILY, "target", now=_JULY_3
        )
        assert again.allowed, "re-admit of the same subject is idempotent at the cap"

    def test_free_plan_is_not_metered_on_the_daily_metric(self, db, enforced):
        """Free has no daily config (3/month makes it meaningless) — the
        metric must fail OPEN for free so the review service can consult
        both buckets unconditionally."""
        player = _make_player(db)
        decision = service.check(db, player, service.METRIC_IMPORT_ANALYSIS_DAILY, now=_JULY_3)
        assert decision.allowed and decision.limit is None


# ---------------------------------------------------------------------------
# 4b. check()/admit() marker consistency (the PR #390 review-quota bug)
# ---------------------------------------------------------------------------


class TestImportAnalysisMarkerConsistency:
    """check() must count the SAME subject-keyed marker rows admit()
    writes for ``import_analysis``.  Before the fix, check() read the
    pure-counter row (``subject == ""``) that no code path ever wrote
    for this metric, so ``entitlement_summary()`` reported
    ``used=0 / remaining=3 / allowed=True`` forever: a capped free user
    saw "3 reviews left", tapped "Try coach review again", the skipped
    re-check re-dispatched the worker, and admit() blocked again — an
    infinite loop with a wrong quota display."""

    def test_check_counts_admitted_subjects(self, db, enforced):
        player = _make_player(db)
        for i in range(2):
            service.admit(db, player, service.METRIC_IMPORT_ANALYSIS, f"ev-{i}", now=_JULY_3)
        decision = service.check(db, player, service.METRIC_IMPORT_ANALYSIS, now=_JULY_3)
        assert (decision.used, decision.remaining, decision.allowed) == (2, 1, True)

    def test_check_blocks_at_marker_limit_and_admit_agrees(self, db, enforced):
        player = _make_player(db)
        for i in range(3):
            admitted = service.admit(
                db, player, service.METRIC_IMPORT_ANALYSIS, f"ev-{i}", now=_JULY_3
            )
            assert admitted.allowed
        blocked = service.check(db, player, service.METRIC_IMPORT_ANALYSIS, now=_JULY_3)
        assert not blocked.allowed
        assert (blocked.used, blocked.remaining) == (3, 0)
        # Both primitives now agree at the boundary — no more
        # "check says go, admit says no" loop.
        fourth = service.admit(db, player, service.METRIC_IMPORT_ANALYSIS, "ev-3", now=_JULY_3)
        assert not fourth.allowed

    def test_re_check_of_admitted_subject_stays_consistent(self, db, enforced):
        """Idempotent re-admits (the review worker re-running a game)
        must not inflate check()'s used count."""
        player = _make_player(db)
        for _ in range(4):
            service.admit(db, player, service.METRIC_IMPORT_ANALYSIS, "same-event", now=_JULY_3)
        decision = service.check(db, player, service.METRIC_IMPORT_ANALYSIS, now=_JULY_3)
        assert (decision.used, decision.remaining, decision.allowed) == (1, 2, True)


# ---------------------------------------------------------------------------
# 5. Distinct-game admission
# ---------------------------------------------------------------------------


class TestCoachedGameAdmission:
    def test_first_game_admits_second_degrades(self, db, enforced):
        player = _make_player(db)

        first = service.admit(db, player, service.METRIC_COACHED_GAME, "game-a", now=_JULY_3)
        assert first.allowed and not first.degrade

        second = service.admit(db, player, service.METRIC_COACHED_GAME, "game-b", now=_JULY_3)
        assert second.allowed, "degrade metrics stay allowed (request proceeds)"
        assert second.degrade

        markers = [r.subject for r in _rows(db)]
        assert markers == ["game-a"], "no marker for the degraded game"

    def test_admission_is_idempotent_per_game(self, db, enforced):
        """Every later move of the admitted game stays admitted; every
        later move of the degraded game stays degraded."""
        player = _make_player(db)
        service.admit(db, player, service.METRIC_COACHED_GAME, "game-a", now=_JULY_3)
        service.admit(db, player, service.METRIC_COACHED_GAME, "game-b", now=_JULY_3)

        for _ in range(5):
            again_a = service.admit(db, player, service.METRIC_COACHED_GAME, "game-a", now=_JULY_3)
            again_b = service.admit(db, player, service.METRIC_COACHED_GAME, "game-b", now=_JULY_3)
            assert again_a.allowed and not again_a.degrade
            assert again_b.degrade

        assert len(_rows(db)) == 1, "idempotent admits must not stack rows"

    def test_missing_game_id_fails_open(self, db, enforced):
        player = _make_player(db)
        for subject in (None, ""):
            decision = service.admit(db, player, service.METRIC_COACHED_GAME, subject, now=_JULY_3)
            assert decision.allowed and not decision.degrade
        assert _rows(db) == []

    def test_new_day_admits_again(self, db, enforced):
        player = _make_player(db)
        service.admit(db, player, service.METRIC_COACHED_GAME, "game-a", now=_JULY_3)
        next_day = service.admit(db, player, service.METRIC_COACHED_GAME, "game-c", now=_JULY_4)
        assert next_day.allowed and not next_day.degrade


# ---------------------------------------------------------------------------
# 6. Pro thresholds
# ---------------------------------------------------------------------------


class TestProThresholds:
    def test_pro_chat_reaches_30(self, db, enforced):
        # 30/day (lowered from 100, 2026-07-06): far above honest use,
        # but chat is the priciest per-unit surface, so the tighter rail
        # halves the pathological token ceiling per subscriber.
        player = _make_player(db, plan="pro")
        # chat_turn is a rolling metric: seed its sentinel-bucket counter
        # in-window (anchored at _JULY_3), not a calendar day bucket.
        _seed_counter(
            db,
            player,
            service.METRIC_CHAT_TURN,
            service._ROLLING_PERIOD,
            29,
            created_at=_JULY_3,
        )
        assert service.check(db, player, service.METRIC_CHAT_TURN, now=_JULY_3).allowed

        service.record(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)
        blocked = service.check(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)
        assert not blocked.allowed
        assert (blocked.plan, blocked.limit, blocked.used) == ("pro", 30, 30)

    def test_pro_admits_ten_games(self, db, enforced):
        player = _make_player(db, plan="pro")
        for i in range(10):
            decision = service.admit(
                db, player, service.METRIC_COACHED_GAME, f"game-{i}", now=_JULY_3
            )
            assert decision.allowed and not decision.degrade
        eleventh = service.admit(db, player, service.METRIC_COACHED_GAME, "game-10", now=_JULY_3)
        assert eleventh.degrade

    def test_pro_imports_50_per_month(self, db, enforced):
        player = _make_player(db, plan="pro")
        # Marker metric: seed 50 distinct admitted subjects — the row shape
        # admit() actually writes (one subject-keyed marker per review).
        for i in range(50):
            service.admit(db, player, service.METRIC_IMPORT_ANALYSIS, f"event-{i}", now=_JULY_3)
        assert not service.check(db, player, service.METRIC_IMPORT_ANALYSIS, now=_JULY_3).allowed


# ---------------------------------------------------------------------------
# 7. set_plan
# ---------------------------------------------------------------------------


class TestSetPlan:
    def test_flips_plan(self, db):
        player = _make_player(db)
        service.set_plan(db, player, "pro")
        db.refresh(player)
        assert player.plan == "pro"

    def test_unknown_plan_rejected(self, db):
        player = _make_player(db)
        with pytest.raises(ValueError):
            service.set_plan(db, player, "platinum")

    def test_commit_failure_rolls_back_and_reraises(self, db, monkeypatch):
        player = _make_player(db)

        def _boom():
            raise SQLAlchemyError("forced commit failure")

        monkeypatch.setattr(db, "commit", _boom)
        with pytest.raises(SQLAlchemyError):
            service.set_plan(db, player, "pro")
        monkeypatch.undo()

        db.rollback()
        db.refresh(player)
        assert player.plan == "free", "failed flip must not persist"


# ---------------------------------------------------------------------------
# 8. Freeze-guard cleanliness
# ---------------------------------------------------------------------------


class TestFreezeGuardCleanliness:
    def test_service_source_trips_no_forbidden_pattern(self):
        """Run the PRODUCTION freeze patterns over the service source.

        The guard scans all of llm.* for RL-shaped code (def train,
        ML-receiver .update).  Reusing FORBIDDEN_PATTERNS here means a
        future edit that would trip the real guard fails this test with
        the offending pattern named, instead of failing at startup."""
        from llm.seca.safety.freeze import FORBIDDEN_PATTERNS

        source_path = Path(service.__file__)
        assert source_path.exists()
        source = source_path.read_text(encoding="utf-8")
        hits = [label for label, pattern in FORBIDDEN_PATTERNS if pattern.search(source)]
        assert hits == [], f"entitlements service trips freeze patterns: {hits}"


# ---------------------------------------------------------------------------
# 9. Rolling 24h windows (coached_game + chat_turn)
# ---------------------------------------------------------------------------


class TestRollingWindows:
    """The daily game + chat limits reset a rolling 24h from FIRST use
    (2026-07-22), not at the UTC calendar boundary, and report ``reset_at``
    for the client countdown.  ``_JULY_4`` is exactly 24h after ``_JULY_3``."""

    def test_chat_resets_24h_from_first_use(self, db, enforced):
        player = _make_player(db)  # free: 3 chats / rolling 24h
        for _ in range(3):
            service.record(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)
        blocked = service.check(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)
        assert not blocked.allowed
        assert (blocked.used, blocked.remaining) == (3, 0)
        assert blocked.reset_at == _JULY_4  # first chat (_JULY_3) + 24h

        # Still blocked 23h in — the window has not elapsed.
        t_23h = datetime(2026, 7, 4, 11, 0, 0)
        assert not service.check(db, player, service.METRIC_CHAT_TURN, now=t_23h).allowed

        # Free again exactly 24h after the first chat.
        freed = service.check(db, player, service.METRIC_CHAT_TURN, now=_JULY_4)
        assert freed.allowed
        assert freed.used == 0
        assert freed.reset_at is None

    def test_expired_window_starts_fresh_on_next_use(self, db, enforced):
        player = _make_player(db)
        for _ in range(3):
            service.record(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)
        # 24h later the old window has rolled off; a new chat opens a fresh one.
        service.record(db, player, service.METRIC_CHAT_TURN, now=_JULY_4)
        after = service.check(db, player, service.METRIC_CHAT_TURN, now=_JULY_4)
        assert after.allowed and after.used == 1  # 1 of 3 in the NEW window
        assert after.reset_at == datetime(2026, 7, 5, 12, 0, 0)  # _JULY_4 + 24h

    def test_game_reset_at_is_the_game_plus_24h(self, db, enforced):
        player = _make_player(db)  # free: 1 coached game / rolling 24h
        service.admit(db, player, service.METRIC_COACHED_GAME, "game-1", now=_JULY_3)
        # A distinct 2nd game is over the rolling limit (the start gate reads
        # remaining==0 → 402); coached_game DEGRADEs so allowed stays True.
        over = service.check(db, player, service.METRIC_COACHED_GAME, now=_JULY_3)
        assert (over.used, over.remaining) == (1, 0)
        assert over.degrade
        assert over.reset_at == _JULY_4
        # 24h later the game rolls off.
        freed = service.check(db, player, service.METRIC_COACHED_GAME, now=_JULY_4)
        assert freed.used == 0
        assert freed.reset_at is None

    def test_game_and_chat_windows_are_independent(self, db, enforced):
        player = _make_player(db)
        for _ in range(3):
            service.record(db, player, service.METRIC_CHAT_TURN, now=_JULY_3)
        assert not service.check(db, player, service.METRIC_CHAT_TURN, now=_JULY_3).allowed
        # The game window is untouched — its slot is still free.
        game = service.check(db, player, service.METRIC_COACHED_GAME, now=_JULY_3)
        assert game.used == 0
        assert game.reset_at is None

    def test_calendar_metric_reports_no_reset_at(self, db, enforced):
        # import_analysis stays MONTHLY calendar-bucketed: no rolling reset.
        player = _make_player(db)
        service.admit(db, player, service.METRIC_IMPORT_ANALYSIS, "ev-1", now=_JULY_3)
        decision = service.check(db, player, service.METRIC_IMPORT_ANALYSIS, now=_JULY_3)
        assert decision.reset_at is None
