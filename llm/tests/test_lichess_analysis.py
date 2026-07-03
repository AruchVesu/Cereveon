"""Tests for the Lichess post-import engine-analysis pass.

Covers ``llm.seca.lichess.analysis_service.analyze_unscored_games`` (the
bounded pass that scores imported games with engine-truth accuracy +
weakness vectors), its integration into ``run_import_job``, and the
post-OAuth-sign-in auto-import kick in ``llm.seca.auth.router``.

The engine pool is faked at the ``evaluate_position`` boundary
(mirroring ``test_pgn_accuracy.py``): an alternating-eval pool makes
every White move a 200 cp mistake so the derived weakness vector is
deterministic and non-empty.

Pinned invariants
-----------------
AN_00  Producer pin: the import stream's explicit ``accuracy=None``
       kwarg persists 0.0 (the ORM's Python-side default fires even for
       explicit None) and the unscored predicate matches it — the
       keystone that connects the stream to the analysis pass.
AN_01  Unscored lichess rows get engine accuracy + phase-keyed
       weaknesses written back.
AN_02  App-source rows and already-scored lichess rows are untouched.
AN_03  The per-job cap analyses the most recently imported rows first;
       older backlog stays unscored for the next job.
AN_04  Engine-pool RuntimeError aborts the pass, keeps prior progress,
       and defers the remaining rows (still unscored).
AN_05  External job cancellation stops the pass before any engine work
       when the job is already terminal at entry.
AN_05b Mid-pass cancellation (status flips while a game is being
       scored) stops the loop before the next game.
AN_06  serialize_job carries the ``analyzed`` counter
       (docs/API_CONTRACTS.md §31).
AN_07  run_import_job without an engine pool skips analysis and still
       succeeds; an analysis crash never fails a completed import.
AN_08  Analysed vectors feed HistoricalAnalysisPipeline: dominant
       category emerges only after the pass runs.
AN_09  /auth/lichess sign-in dispatches an import worker exactly once
       (via start_import_job's dispatch callback) with the app-state
       engine pool.
AN_10  Import-kick failure never fails the sign-in (best-effort).
AN_11  The analysis pass never mutates Player.rating / confidence.
AN_12  A ValueError from the recompute (corrupt PGN) writes the neutral
       fallback (0.5 / empty) so the row leaves the unscored set, and
       the pass continues with the next row.
PL_01  analysis_service's local platform constant matches
       import_service.PLATFORM_LICHESS (kept separate to stay
       import-cycle-free).

Deliberately untested: a TRUE concurrent unlink during an in-flight
engine evaluation — the per-game cancellation granularity (up to one
game of engine time after the flip) is documented in
analysis_service.analyze_unscored_games and mirrors the import stream's
contract.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request as StarletteRequest

# Import all model modules so Base.metadata sees every table.
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401
import llm.seca.lichess.models  # noqa: F401

from llm.seca.analysis.historical_pipeline import HistoricalAnalysisPipeline
from llm.seca.auth.models import Base, Player
from llm.seca.auth.router import (
    LichessLoginRequest,
    _maybe_backfill_lichess_import,
    login_lichess,
    me,
)
from llm.seca.events.models import GameEvent
from llm.seca.lichess import analysis_service
from llm.seca.lichess import client as lichess_client
from llm.seca.lichess import import_service
from llm.seca.lichess.models import (
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    LichessImportJob,
    LinkedAccount,
)
from llm.seca.shared_limiter import limiter

# ---------------------------------------------------------------------------
# Fixtures + fakes
# ---------------------------------------------------------------------------

# Same representative PGN the lichess import tests use — parseable by
# python-chess, 6 plies (3 White moves), all in the opening phase.
_VALID_PGN = (
    '[Event "Casual"]\n'
    '[Site "https://lichess.org/abc12345"]\n'
    '[Date "2026.01.01"]\n'
    '[White "alice"]\n'
    '[Black "bob"]\n'
    '[Result "1-0"]\n'
    "\n"
    "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0\n"
)

# Engine calls compute_accuracy_from_pgn issues for _VALID_PGN: one for
# the starting position + one per ply.
_EVALS_PER_GAME = 7


class _AlternatingEvalPool:
    """Fake engine pool: evals alternate 0 / -200 cp per call.

    With the player inferred as White (result="win" + Result "1-0"),
    every White move transitions the eval from 0 to -200 — a 200 cp
    loss, above the 150 cp mistake threshold — so the pass produces a
    deterministic non-empty weakness vector ({"opening": 1.0} for the
    all-opening test PGN) and accuracy 1/(1+200/100) = 1/3.
    """

    def __init__(self) -> None:
        self.call_count = 0

    def evaluate_position(
        self,
        *,
        fen: str,
        movetime_ms: int,
        queue_timeout_ms: int | None = None,
    ) -> dict:
        self.call_count += 1
        value = 0 if self.call_count % 2 == 1 else -200
        return {"evaluation": {"type": "cp", "value": value}}


class _ExplodingPool:
    """Fake pool whose Nth call raises RuntimeError (pool saturation)."""

    def __init__(self, explode_on_call: int) -> None:
        self.call_count = 0
        self.explode_on_call = explode_on_call

    def evaluate_position(
        self,
        *,
        fen: str,
        movetime_ms: int,
        queue_timeout_ms: int | None = None,
    ) -> dict:
        self.call_count += 1
        if self.call_count >= self.explode_on_call:
            raise RuntimeError("engine pool exhausted")
        return {"evaluation": {"type": "cp", "value": 0}}


class _CancellingPool:
    """Fake pool that flips the job terminal partway through a game.

    Exercises the MID-PASS cancellation seam: the flip happens while
    game 1 is being scored, so the between-games status check must stop
    the loop before game 2.
    """

    def __init__(self, db, job, flip_on_call: int) -> None:
        self.db = db
        self.job = job
        self.flip_on_call = flip_on_call
        self.call_count = 0

    def evaluate_position(
        self,
        *,
        fen: str,
        movetime_ms: int,
        queue_timeout_ms: int | None = None,
    ) -> dict:
        self.call_count += 1
        if self.call_count == self.flip_on_call:
            self.job.status = JOB_STATUS_FAILED
            self.db.commit()
        return {"evaluation": {"type": "cp", "value": 0}}


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
        email="analysis@test.com",
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


def _insert_lichess_game(
    db, player, *, external_id: str, accuracy=None, weaknesses="{}", created_at=None
):
    """Construct a row exactly as _run_import_stream does (explicit
    ``accuracy=None`` kwarg — which the ORM persists as 0.0, see AN_00)."""
    row = GameEvent(
        player_id=player.id,
        pgn=_VALID_PGN,
        result="win",
        accuracy=accuracy,
        weaknesses_json=weaknesses,
        source=import_service.PLATFORM_LICHESS,
        external_game_id=external_id,
    )
    db.add(row)
    db.commit()
    if created_at is not None:
        row.created_at = created_at
        db.commit()
    db.refresh(row)
    return row


def _is_unscored(row) -> bool:
    """The on-disk shape imported rows carry before the pass scores them."""
    return row.accuracy in (None, 0.0) and (row.weaknesses_json or "{}") == "{}"


@contextmanager
def _limiter_disabled():
    prev = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = prev


# ---------------------------------------------------------------------------
# analyze_unscored_games
# ---------------------------------------------------------------------------


class TestAnalyzeUnscoredGames:
    def test_an_00_producer_default_pins_unscored_predicate(self, db_session, player):
        # The keystone invariant: the stream's explicit accuracy=None
        # kwarg persists as 0.0 because the column's Python-side default
        # fires — NOT as NULL.  The unscored predicate must match it,
        # and must NOT match a scored row.
        row = _insert_lichess_game(db_session, player, external_id="g0")
        db_session.refresh(row)
        assert row.accuracy == 0.0  # ORM default fired despite explicit None

        matched = (
            db_session.query(GameEvent)
            .filter(
                GameEvent.player_id == player.id,
                GameEvent.source == import_service.PLATFORM_LICHESS,
                analysis_service._unscored_filter(),
            )
            .all()
        )
        assert [r.id for r in matched] == [row.id]

        row.accuracy = 1.0 / 3.0  # what a scored row carries
        db_session.commit()
        assert (
            db_session.query(GameEvent)
            .filter(
                GameEvent.player_id == player.id,
                analysis_service._unscored_filter(),
            )
            .count()
            == 0
        )

    def test_pl_01_platform_constant_matches(self):
        assert analysis_service._PLATFORM_LICHESS == import_service.PLATFORM_LICHESS

    def test_an_01_unscored_rows_get_accuracy_and_weaknesses(self, db_session, player):
        row = _insert_lichess_game(db_session, player, external_id="g1")
        analyzed = analysis_service.analyze_unscored_games(
            db_session, player, _AlternatingEvalPool()
        )
        assert analyzed == 1
        db_session.refresh(row)
        assert row.accuracy == pytest.approx(1.0 / 3.0)
        weaknesses = json.loads(row.weaknesses_json)
        assert weaknesses == {"opening": pytest.approx(1.0)}

    def test_an_02_scored_and_app_rows_untouched(self, db_session, player):
        scored = _insert_lichess_game(
            db_session, player, external_id="g2", accuracy=0.9, weaknesses='{"endgame": 0.5}'
        )
        # Unscored-shaped but source='app': the pass must never touch it.
        app_row = GameEvent(
            player_id=player.id,
            pgn=_VALID_PGN,
            result="win",
            accuracy=None,
            weaknesses_json="{}",
            source="app",
        )
        db_session.add(app_row)
        db_session.commit()

        analyzed = analysis_service.analyze_unscored_games(
            db_session, player, _AlternatingEvalPool()
        )
        assert analyzed == 0
        db_session.refresh(scored)
        db_session.refresh(app_row)
        assert scored.accuracy == pytest.approx(0.9)
        assert json.loads(scored.weaknesses_json) == {"endgame": 0.5}
        assert _is_unscored(app_row)  # still carries the untouched marker

    def test_an_03_cap_analyses_most_recently_imported_first(self, db_session, player):
        old = _insert_lichess_game(
            db_session, player, external_id="g-old", created_at=datetime(2026, 1, 1)
        )
        new = _insert_lichess_game(
            db_session,
            player,
            external_id="g-new",
            created_at=datetime(2026, 1, 1) + timedelta(days=1),
        )

        analyzed = analysis_service.analyze_unscored_games(
            db_session, player, _AlternatingEvalPool(), max_games_analyzed=1
        )
        assert analyzed == 1
        db_session.refresh(old)
        db_session.refresh(new)
        assert new.accuracy == pytest.approx(1.0 / 3.0)
        assert _is_unscored(old)  # deferred to the next job

    def test_an_04_pool_error_defers_remaining_rows(self, db_session, player):
        first = _insert_lichess_game(
            db_session, player, external_id="g-a", created_at=datetime(2026, 1, 2)
        )
        second = _insert_lichess_game(
            db_session, player, external_id="g-b", created_at=datetime(2026, 1, 1)
        )

        # All of game one's evals succeed; the first eval of game two
        # explodes (pool saturation mid-pass).
        pool = _ExplodingPool(explode_on_call=_EVALS_PER_GAME + 1)
        analyzed = analysis_service.analyze_unscored_games(db_session, player, pool)
        assert analyzed == 1
        db_session.refresh(first)
        db_session.refresh(second)
        assert first.accuracy is not None and first.accuracy > 0.0
        assert _is_unscored(second)  # deferred, still unscored

    def test_an_05_terminal_job_stops_before_any_engine_work(self, db_session, player):
        _insert_lichess_game(db_session, player, external_id="g-c")
        job = LichessImportJob(
            player_id=player.id,
            status=JOB_STATUS_FAILED,  # e.g. unlink cancelled mid-run
            target_max_games=50,
        )
        db_session.add(job)
        db_session.commit()

        pool = _AlternatingEvalPool()
        analyzed = analysis_service.analyze_unscored_games(db_session, player, pool, job=job)
        assert analyzed == 0
        assert pool.call_count == 0  # stopped before any engine work

    def test_an_05b_mid_pass_cancellation_stops_before_next_game(self, db_session, player):
        _insert_lichess_game(
            db_session, player, external_id="g-m1", created_at=datetime(2026, 1, 2)
        )
        untouched = _insert_lichess_game(
            db_session, player, external_id="g-m2", created_at=datetime(2026, 1, 1)
        )
        job = LichessImportJob(
            player_id=player.id,
            status=JOB_STATUS_RUNNING,
            target_max_games=50,
        )
        db_session.add(job)
        db_session.commit()

        # The flip lands mid-way through game 1's evals; the pass must
        # finish that game (per-game granularity) and stop before game 2.
        pool = _CancellingPool(db_session, job, flip_on_call=3)
        analyzed = analysis_service.analyze_unscored_games(db_session, player, pool, job=job)
        assert analyzed == 1
        assert pool.call_count == _EVALS_PER_GAME  # game 2 never started
        db_session.refresh(untouched)
        assert _is_unscored(untouched)
        # The zombie counter bump on an already-failed job is accepted
        # (documented per-game granularity) — status is what matters:
        db_session.refresh(job)
        assert job.status == JOB_STATUS_FAILED

    def test_an_06_serialize_job_carries_analyzed(self, db_session, player):
        job = LichessImportJob(
            player_id=player.id,
            status=JOB_STATUS_RUNNING,
            target_max_games=50,
            analyzed=7,
        )
        db_session.add(job)
        db_session.commit()
        payload = import_service.serialize_job(job)
        assert payload["analyzed"] == 7

    def test_an_08_analysis_feeds_historical_pipeline(self, db_session, player):
        row = _insert_lichess_game(db_session, player, external_id="g-hist")

        before = HistoricalAnalysisPipeline(db_session).run(str(player.id), [row])
        assert before.dominant_category is None

        analysis_service.analyze_unscored_games(db_session, player, _AlternatingEvalPool())
        db_session.refresh(row)
        after = HistoricalAnalysisPipeline(db_session).run(str(player.id), [row])
        assert after.phase_rates.get("opening", 0.0) > 0.0
        assert after.dominant_category is not None

    def test_an_11_rating_and_confidence_never_mutated(self, db_session, player):
        _insert_lichess_game(db_session, player, external_id="g-r")
        analysis_service.analyze_unscored_games(db_session, player, _AlternatingEvalPool())
        db_session.refresh(player)
        assert player.rating == pytest.approx(1200.0)
        assert player.confidence == pytest.approx(0.5)

    def test_an_12_value_error_writes_neutral_fallback_and_continues(
        self, db_session, player, monkeypatch
    ):
        bad = _insert_lichess_game(
            db_session, player, external_id="g-bad", created_at=datetime(2026, 1, 2)
        )
        good = _insert_lichess_game(
            db_session, player, external_id="g-good", created_at=datetime(2026, 1, 1)
        )

        real = analysis_service.compute_accuracy_from_pgn
        calls = {"n": 0}

        def flaky(pgn, pool, *, result, movetime_ms):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("simulated corrupt PGN")
            return real(pgn, pool, result=result, movetime_ms=movetime_ms)

        monkeypatch.setattr(analysis_service, "compute_accuracy_from_pgn", flaky)
        analyzed = analysis_service.analyze_unscored_games(
            db_session, player, _AlternatingEvalPool()
        )
        # The fallback-scored row does NOT count as analysed, but it
        # leaves the unscored set so it can't wedge the cap forever.
        assert analyzed == 1
        db_session.refresh(bad)
        db_session.refresh(good)
        assert bad.accuracy == pytest.approx(0.5)
        assert bad.weaknesses_json == "{}"
        assert not _is_unscored(bad)
        assert good.accuracy == pytest.approx(1.0 / 3.0)


# ---------------------------------------------------------------------------
# run_import_job integration seams
# ---------------------------------------------------------------------------


class TestRunImportJobAnalysisSeam:
    def _run_job(self, db_session, player, monkeypatch, *, engine_pool, analysis_fn) -> str:
        """Drive run_import_job with the worker session + stream faked.

        Returns the job id; assert on a re-fetched row — run_import_job
        closes its session in ``finally``, which detaches every loaded
        instance (SQLAlchemy 2.x ``close`` expunges).
        """
        job = LichessImportJob(
            player_id=player.id,
            status=JOB_STATUS_QUEUED,
            target_max_games=50,
        )
        db_session.add(job)
        # The worker looks the LinkedAccount up itself.
        db_session.add(
            LinkedAccount(
                player_id=player.id,
                platform=import_service.PLATFORM_LICHESS,
                external_username="analysisuser",
            )
        )
        db_session.commit()
        job_id = job.id

        monkeypatch.setattr(import_service, "_WorkerSession", lambda: db_session)
        monkeypatch.setattr(
            import_service, "_run_import_stream", lambda *a, **kw: {"inserted": 0}
        )
        monkeypatch.setattr(analysis_service, "analyze_unscored_games", analysis_fn)
        import_service.run_import_job(job_id, max_games=50, engine_pool=engine_pool)
        return job_id

    def test_an_07_no_pool_skips_analysis_and_succeeds(self, db_session, player, monkeypatch):
        def _must_not_run(*a, **kw):  # pragma: no cover — asserts by raising
            raise AssertionError("analysis pass ran without an engine pool")

        job_id = self._run_job(
            db_session, player, monkeypatch, engine_pool=None, analysis_fn=_must_not_run
        )
        refetched = db_session.get(LichessImportJob, job_id)
        assert refetched is not None
        assert refetched.status == JOB_STATUS_SUCCEEDED

    def test_an_07b_analysis_crash_never_fails_completed_import(
        self, db_session, player, monkeypatch
    ):
        def _explode(*a, **kw):
            raise RuntimeError("unexpected analysis crash")

        job_id = self._run_job(
            db_session,
            player,
            monkeypatch,
            engine_pool=_AlternatingEvalPool(),
            analysis_fn=_explode,
        )
        refetched = db_session.get(LichessImportJob, job_id)
        assert refetched is not None
        assert refetched.status == JOB_STATUS_SUCCEEDED


# ---------------------------------------------------------------------------
# /auth/lichess auto-import kick
# ---------------------------------------------------------------------------

VALID_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

ACCOUNT_JSON = {
    "id": "analysisuser",
    "username": "AnalysisUser",
    "perfs": {"rapid": {"games": 120, "rating": 1907, "prov": False}},
}


class _FakeExecutor:
    def __init__(self) -> None:
        self.submits: list[tuple] = []

    def submit(self, fn, *args, **kwargs):
        self.submits.append((fn, args, kwargs))


def _request_with_app_state() -> StarletteRequest:
    """Request whose scope carries an app.state.engine_pool sentinel."""
    app = SimpleNamespace(state=SimpleNamespace(engine_pool="POOL-SENTINEL"))
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/lichess",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "app": app,
        }
    )


def _patch_oauth_success(monkeypatch):
    monkeypatch.setattr(
        lichess_client, "exchange_authorization_code", lambda code, verifier: "lio_testtoken"
    )
    monkeypatch.setattr(lichess_client, "fetch_account", lambda token: dict(ACCOUNT_JSON))
    monkeypatch.setattr(lichess_client, "revoke_token", lambda token: None)


class TestSignInAutoImport:
    def test_an_09_sign_in_dispatches_import_worker_once(self, db_session, monkeypatch):
        _patch_oauth_success(monkeypatch)
        import llm.seca.lichess.router as lichess_router

        fake_executor = _FakeExecutor()
        monkeypatch.setattr(lichess_router, "_executor", fake_executor)

        with _limiter_disabled():
            result = login_lichess(
                request=_request_with_app_state(),
                req=LichessLoginRequest(code="auth-code-abc123", code_verifier=VALID_VERIFIER),
                db=db_session,
            )
        assert result["created"] is True

        job = (
            db_session.query(LichessImportJob)
            .filter_by(player_id=result["player_id"])
            .one()
        )
        assert job.status == JOB_STATUS_QUEUED
        assert len(fake_executor.submits) == 1
        fn, args, kwargs = fake_executor.submits[0]
        assert fn is import_service.run_import_job
        assert args == (job.id,)
        assert kwargs["engine_pool"] == "POOL-SENTINEL"

    def test_an_10_kick_failure_never_fails_sign_in(self, db_session, monkeypatch):
        _patch_oauth_success(monkeypatch)

        def _explode(*a, **kw):
            raise RuntimeError("import service down")

        monkeypatch.setattr(import_service, "start_import_job", _explode)
        with _limiter_disabled():
            result = login_lichess(
                request=_request_with_app_state(),
                req=LichessLoginRequest(code="auth-code-abc123", code_verifier=VALID_VERIFIER),
                db=db_session,
            )
        assert result["created"] is True
        assert db_session.query(LichessImportJob).count() == 0


# ---------------------------------------------------------------------------
# Cold-start one-time backfill (GET /auth/me) for pre-existing links
# ---------------------------------------------------------------------------


class TestColdStartBackfill:
    """`_maybe_backfill_lichess_import` — the /auth/me hook that pulls
    history for accounts linked before auto-import-on-sign-in shipped.

    BF_01  linked + zero imported games -> kicks a one-time import.
    BF_02  linked + >=1 imported game    -> no kick (one-time gate).
    BF_03  not linked                    -> no kick.
    BF_04  me(player=...) still returns the profile by direct call — the
           backfill is a route DEPENDENCY, so it never runs on the direct
           call that test_api_contract_validation / test_auth_update_me
           rely on.
    """

    def _link(self, db, player):
        db.add(
            LinkedAccount(
                player_id=player.id,
                platform=import_service.PLATFORM_LICHESS,
                external_username="backfilluser",
            )
        )
        db.commit()

    def test_bf_01_linked_zero_games_kicks_backfill(self, db_session, player, monkeypatch):
        import llm.seca.lichess.router as lichess_router

        fake_executor = _FakeExecutor()
        monkeypatch.setattr(lichess_router, "_executor", fake_executor)
        self._link(db_session, player)

        _maybe_backfill_lichess_import(db_session, player, _request_with_app_state())

        job = db_session.query(LichessImportJob).filter_by(player_id=player.id).one()
        assert job.status == JOB_STATUS_QUEUED
        assert len(fake_executor.submits) == 1
        fn, args, kwargs = fake_executor.submits[0]
        assert fn is import_service.run_import_job
        assert args == (job.id,)
        assert kwargs["engine_pool"] == "POOL-SENTINEL"

    def test_bf_02_existing_games_skip_backfill(self, db_session, player, monkeypatch):
        import llm.seca.lichess.router as lichess_router

        fake_executor = _FakeExecutor()
        monkeypatch.setattr(lichess_router, "_executor", fake_executor)
        self._link(db_session, player)
        # One already-imported Lichess game -> the one-time gate must hold.
        db_session.add(
            GameEvent(
                player_id=player.id,
                pgn=_VALID_PGN,
                result="win",
                accuracy=0.5,
                weaknesses_json="{}",
                source=import_service.PLATFORM_LICHESS,
                external_game_id="already-1",
            )
        )
        db_session.commit()

        _maybe_backfill_lichess_import(db_session, player, _request_with_app_state())

        assert db_session.query(LichessImportJob).count() == 0
        assert len(fake_executor.submits) == 0

    def test_bf_03_unlinked_skips_backfill(self, db_session, player, monkeypatch):
        import llm.seca.lichess.router as lichess_router

        fake_executor = _FakeExecutor()
        monkeypatch.setattr(lichess_router, "_executor", fake_executor)
        # No LinkedAccount for this player.

        _maybe_backfill_lichess_import(db_session, player, _request_with_app_state())

        assert db_session.query(LichessImportJob).count() == 0
        assert len(fake_executor.submits) == 0

    def test_bf_04_me_direct_call_still_returns_profile(self):
        # The backfill is wired as a route dependency, so a direct
        # me(player=...) call (no request/db) must keep working — this is
        # the shape test_api_contract_validation & friends rely on.
        player = SimpleNamespace(
            id="p-direct",
            email="direct@test.com",
            rating=1234.0,
            confidence=0.5,
            skill_vector_json="{}",
            training_xp=7,
        )
        result = me(player=player)
        assert result["id"] == "p-direct"
        assert result["email"] == "direct@test.com"
        assert result["training_xp"] == 7
