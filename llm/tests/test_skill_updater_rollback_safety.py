"""
Regression tests for the 2026-05-15 ``/game/finish`` 500 incident.

Two bugs cooperated to produce the outage:

  Bug A (root cause)
  ------------------
  ``llm.seca.brain.bandit.experience_store.ExperienceStore.log`` issued a
  raw ``CREATE TABLE IF NOT EXISTS bandit_experiences (id INTEGER
  PRIMARY KEY AUTOINCREMENT, ...)`` on every call.  AUTOINCREMENT is
  SQLite-only DDL; Postgres rejects it at parse time *before* the
  ``IF NOT EXISTS`` guard fires, so every call into SkillUpdater on
  prod aborted the SQLAlchemy txn.  The table itself was already being
  created portably by ``Base.metadata.create_all`` via the
  ``BanditExperience`` ORM model — the raw DDL was redundant and broken.

  Bug B (cascade)
  ---------------
  ``llm.seca.events.router.finish_game`` wrapped SkillUpdater in
  ``try/except`` but never called ``db.rollback()``.  After Bug A
  fired, the next ORM call (``db.refresh(player)``) hit
  ``InFailedSqlTransaction`` and ``/game/finish`` returned HTTP 500
  despite the GameEvent + skill-update guard already swallowing
  the original exception.

Pinned invariants
-----------------
 1. EXPSTORE_NO_DDL          ExperienceStore.log MUST NOT execute any
                             ``CREATE TABLE`` statement — the schema is
                             owned by ``BanditExperience`` on ``Base``.
 2. EXPSTORE_INSERT_ONLY     The only statement ExperienceStore.log
                             emits is the parameterised INSERT.
 3. FINISH_ROLLBACK_ON_FAIL  finish_game MUST call db.rollback() when
                             SkillUpdater.update_from_event raises, so
                             the next ORM call (db.refresh) cannot
                             cascade with InFailedSqlTransaction.
 4. FINISH_RESPONDS_AFTER_FAIL finish_game still returns a populated
                             response dict after a SkillUpdater failure
                             — the GameEvent already committed and the
                             coach content path must run end-to-end.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from llm.seca.brain.bandit.experience_store import ExperienceStore
from llm.seca.events.router import GameFinishRequest, finish_game
from llm.seca.shared_limiter import limiter


# ---------------------------------------------------------------------------
# Stub request — mirrors test_pgn_accuracy.py:_StubRequest.  A bare
# starlette.requests.Request with no FastAPI app attached raises
# KeyError on ``request.app`` access, which finish_game does inside
# _resolve_authoritative_accuracy.  We pass engine_pool=None so the
# resolver takes the client-value fallback branch (sufficient for
# the rollback safety assertions).
# ---------------------------------------------------------------------------


class _StubAppState:
    def __init__(self, engine_pool=None):
        if engine_pool is not None:
            self.engine_pool = engine_pool


class _StubApp:
    def __init__(self, engine_pool=None):
        self.state = _StubAppState(engine_pool)


class _StubRequest:
    def __init__(self, engine_pool=None):
        self.app = _StubApp(engine_pool)


# ---------------------------------------------------------------------------
# 1.  EXPSTORE_NO_DDL / EXPSTORE_INSERT_ONLY
# ---------------------------------------------------------------------------


class TestExperienceStoreNoDDL:
    """ExperienceStore.log must not run ``CREATE TABLE`` — the schema is
    owned by ``BanditExperience`` on the SQLAlchemy ``Base``."""

    def _capture_executed_sql(self) -> tuple[ExperienceStore, list[str]]:
        executed: list[str] = []
        db = MagicMock()

        def _record(stmt, params=None):
            executed.append(str(stmt))
            return MagicMock()

        db.execute.side_effect = _record
        return ExperienceStore(db), executed

    def test_log_does_not_emit_create_table(self):
        """EXPSTORE_NO_DDL — no CREATE TABLE in the SQL stream."""
        store, executed = self._capture_executed_sql()
        store.log(
            player_id="p1",
            context=np.array([0.1, 0.2, 0.3]),
            action="opening",
            reward=0.5,
        )
        for sql in executed:
            assert "CREATE TABLE" not in sql.upper(), (
                "ExperienceStore.log emitted a CREATE TABLE statement — the "
                "AUTOINCREMENT-bearing raw DDL was reintroduced.  Schema is "
                "owned by BanditExperience on Base; do not duplicate it."
            )

    def test_log_emits_only_insert(self):
        """EXPSTORE_INSERT_ONLY — exactly one INSERT statement is sent."""
        store, executed = self._capture_executed_sql()
        store.log(
            player_id="p1",
            context=np.array([0.0]),
            action="endgame",
            reward=-0.25,
        )
        assert len(executed) == 1, (
            f"ExperienceStore.log issued {len(executed)} statements, "
            f"expected exactly 1 (the parameterised INSERT): {executed!r}"
        )
        assert "INSERT INTO bandit_experiences" in executed[0]


# ---------------------------------------------------------------------------
# 2.  FINISH_ROLLBACK_ON_FAIL / FINISH_RESPONDS_AFTER_FAIL
# ---------------------------------------------------------------------------
#
# These tests exercise the actual ``finish_game`` handler with a
# MagicMock-backed session and a broken SkillUpdater.  Even though the
# in-memory mock cannot reproduce Postgres' aborted-txn behaviour, we
# can assert the load-bearing call (``db.rollback``) happens — which is
# what the next-call cascade defends against.


_VALID_PGN = (
    '[Event "Test"]\n'
    '[Site "?"]\n'
    '[Date "2026.05.15"]\n'
    '[Round "1"]\n'
    '[White "Probe"]\n'
    '[Black "Bot"]\n'
    '[Result "1-0"]\n'
    "\n"
    "1. e4 e5 2. Nf3 Nc6 1-0"
)


def _make_player_and_db():
    """Minimal player + auto-mock db.  Mirrors
    test_game_finish_resume_link.py:_make_player_and_db so the handler
    can run end-to-end without a real DB."""
    player = SimpleNamespace(id="player-rollback", rating=1500.0, confidence=0.5)

    def _fake_refresh(obj):
        if obj is player:
            player.rating = 1510.0
            player.confidence = 0.55

    db = MagicMock()
    db.refresh.side_effect = _fake_refresh
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
        []
    )
    return player, db


def _invoke_finish_with_broken_updater(exception_factory):
    """Run finish_game with SkillUpdater patched to raise.

    Returns ``(response_dict, db_mock)`` so callers can inspect both
    the handler's output and the rollback call surface."""
    player, db = _make_player_and_db()
    req = GameFinishRequest(
        pgn=_VALID_PGN,
        result="win",
        accuracy=0.8,
        weaknesses={},
    )
    request = _StubRequest(engine_pool=None)
    fake_event = SimpleNamespace(id=42)

    broken_updater = MagicMock()
    broken_updater.return_value.update_from_event.side_effect = exception_factory()

    prev = limiter.enabled
    limiter.enabled = False
    try:
        with (
            patch("llm.seca.events.router.EventStorage") as MockStorage,
            patch("llm.seca.events.router.SkillUpdater", broken_updater),
        ):
            MockStorage.return_value.store_game.return_value = fake_event
            MockStorage.return_value.get_recent_games.return_value = []
            from fastapi import BackgroundTasks as _BackgroundTasks  # noqa: PLC0415

            result = finish_game(
                req=req,
                request=request,
                background_tasks=_BackgroundTasks(),
                player=player,
                db=db,
            )
    finally:
        limiter.enabled = prev

    return result, db


class TestFinishGameRollbackSafety:
    """finish_game must rollback after a SkillUpdater failure so the
    following db.refresh(player) call cannot cascade with
    InFailedSqlTransaction on Postgres."""

    def test_rollback_called_when_skill_updater_raises(self):
        """FINISH_ROLLBACK_ON_FAIL — db.rollback() is invoked exactly
        once after the except clause swallows the SkillUpdater error."""
        _, db = _invoke_finish_with_broken_updater(
            lambda: RuntimeError("simulated InFailedSqlTransaction precursor")
        )
        assert db.rollback.call_count >= 1, (
            "finish_game did NOT call db.rollback() after SkillUpdater "
            "raised.  On Postgres, this lets the aborted txn cascade "
            "through db.refresh(player) and 500 the route — exactly the "
            "regression seen on prod 2026-05-15."
        )

    def test_rollback_called_for_generic_exception(self):
        """Same guarantee for a non-RuntimeError failure path."""
        _, db = _invoke_finish_with_broken_updater(
            lambda: Exception("UNIQUE constraint failed: experiences.id")
        )
        assert db.rollback.call_count >= 1

    def test_finish_still_responds_after_skill_updater_failure(self):
        """FINISH_RESPONDS_AFTER_FAIL — the handler returns a full
        response dict even when SkillUpdater raises."""
        result, _ = _invoke_finish_with_broken_updater(
            lambda: RuntimeError("simulated SkillUpdater failure")
        )
        assert isinstance(result, dict), (
            f"finish_game returned non-dict {type(result).__name__} "
            "after SkillUpdater failure — the response build path was "
            "skipped, which would surface as 500 to the client."
        )
        for key in ("new_rating", "confidence", "coach_action", "coach_content"):
            assert key in result, (
                f"finish_game response missing {key!r} after SkillUpdater "
                f"failure; keys present: {sorted(result.keys())}"
            )
