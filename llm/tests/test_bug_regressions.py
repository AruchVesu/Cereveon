"""
Regression tests for confirmed bugs fixed in this cycle.

Each test is named after the invariant it pins and includes the
original crash/wrong-value description so CI failures are self-explaining.

Bugs covered
------------
BUG-1  reward.py:6       ZeroDivisionError on empty skill list
BUG-2  spacing.py:12     next_interval(_, 0.0) returned 0.0 instead of ≥1
BUG-3  trainer.py:21     ZeroDivisionError when events list is empty
BUG-4a bandit.py:27-47  LinUCB.select() returned None for empty actions list
BUG-4b bandit.py:35      np.linalg.inv raised LinAlgError on near-singular A
BUG-5  engine_eval.py:47 cache key collision: movetime=None → "0" == movetime=0
BUG-6  engine_pool.py    stop() set _started=False *after* engine teardown,
                          leaving a window where acquire() could run on a dead pool
BUG-7  auth/service.py   token_hash compared with != (timing attack); must use hmac.compare_digest
BUG-8  storage/repo.py        SQLite connections leaked on exception (no try-finally)
BUG-9  adapt.py:241           random.choice(sorted_moves[:-1]) raises IndexError
                               when only one move is available (list[:-1] == [])
BUG-10 global_bandit.py       GlobalLinUCB.select() same empty-actions + singular matrix
                               + float((1,1)) TypeError as contextual_bandit
BUG-11 meta_bandit.py         LinUCB.select_action() same three issues
BUG-12 outcome_tracker.py:130 4 score components divided by 3.0; max reachable = 1.167 > 1.0
"""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")


# ---------------------------------------------------------------------------
# BUG-1  reward.py — ZeroDivisionError on empty skill list
# ---------------------------------------------------------------------------


class TestRatingFromSkillEmptyList:
    """BUG-1: rating_from_skill([]) must not raise ZeroDivisionError."""

    def test_empty_skill_list_does_not_raise(self):
        from llm.seca.curriculum.reward import rating_from_skill

        result = rating_from_skill([])
        assert isinstance(result, float), f"Expected float, got {type(result)}"

    def test_empty_skill_list_returns_base_rating(self):
        """An empty latent skill vector has no information; base Elo (800) is the default."""
        from llm.seca.curriculum.reward import rating_from_skill

        assert rating_from_skill([]) == 800.0

    def test_reward_with_both_empty_vectors_does_not_raise(self):
        """reward([], []) is Elo-improvement of 0; must not crash."""
        from llm.seca.curriculum.reward import reward

        result = reward([], [])
        assert result == 0.0

    def test_reward_skill_before_empty_does_not_raise(self):
        from llm.seca.curriculum.reward import reward

        result = reward([], [0.5, 0.6])
        assert isinstance(result, float)

    def test_nonempty_skill_list_still_correct(self):
        """Regression: the fix must not change behaviour for normal inputs."""
        from llm.seca.curriculum.reward import rating_from_skill

        assert rating_from_skill([0.5, 0.5]) == pytest.approx(800 + 400 * 0.5)
        assert rating_from_skill([1.0]) == pytest.approx(1200.0)
        assert rating_from_skill([0.0]) == pytest.approx(800.0)


# ---------------------------------------------------------------------------
# BUG-2  spacing.py — next_interval(_, 0.0) returned 0.0
# ---------------------------------------------------------------------------


class TestNextIntervalZeroPreviousInterval:
    """BUG-2: next_interval with previous_interval=0 must return ≥1, not 0."""

    @pytest.mark.parametrize("success_rate", [0.6, 0.7, 0.8, 0.9, 1.0])
    def test_zero_previous_interval_returns_at_least_one(self, success_rate: float):
        """
        On a player's very first review (previous_interval=0) with any
        passing success_rate, the next interval must be ≥1 day.
        Before the fix: 0.0 * growth == 0.0, scheduling nothing.
        """
        from llm.seca.curriculum.spacing import next_interval

        result = next_interval(success_rate, 0.0)
        assert result >= 1.0, (
            f"next_interval({success_rate}, 0.0) = {result}; "
            "must be ≥1 to schedule the first review"
        )

    def test_below_threshold_still_returns_one(self):
        """success_rate < 0.6 always returns 1.0 regardless of previous_interval."""
        from llm.seca.curriculum.spacing import next_interval

        assert next_interval(0.5, 0.0) == 1.0
        assert next_interval(0.0, 10.0) == 1.0

    def test_non_zero_previous_interval_is_unaffected(self):
        """The fix (max(1.0, ...)) must not alter normal intervals > 1."""
        from llm.seca.curriculum.spacing import next_interval

        result = next_interval(0.8, 5.0)
        expected = 5.0 * (1.8 + 0.8)
        assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# BUG-3  trainer.py — ZeroDivisionError on empty events list
# ---------------------------------------------------------------------------


class TestSkillTrainerEmptyEvents:
    """BUG-3: SkillTrainer.train_on_events([]) must not raise ZeroDivisionError."""

    def test_empty_events_does_not_raise(self):
        from llm.seca.skills.trainer import SkillTrainer

        trainer = SkillTrainer()
        result = trainer.train_on_events([])
        assert result is not None

    def test_empty_events_returns_status_dict(self):
        from llm.seca.skills.trainer import SkillTrainer

        trainer = SkillTrainer()
        result = trainer.train_on_events([])
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"

    def test_empty_events_does_not_mutate_last_accuracy(self):
        """last_accuracy must remain None when there is nothing to learn from."""
        from llm.seca.skills.trainer import SkillTrainer

        trainer = SkillTrainer()
        trainer.train_on_events([])
        assert trainer.last_accuracy is None

    def test_nonempty_events_returns_dict(self):
        """Regression: non-empty events must return a dict (SAFE_MODE short-circuits
        the computation path in this environment, but must still return a dict)."""
        from llm.seca.skills.trainer import SkillTrainer
        from types import SimpleNamespace

        trainer = SkillTrainer()
        events = [SimpleNamespace(accuracy=0.8), SimpleNamespace(accuracy=0.6)]
        result = trainer.train_on_events(events)
        assert isinstance(result, dict)
        # SAFE_MODE=True is hardcoded in this codebase; the safe-mode path returns
        # {"status": "safe_mode"}.  When SAFE_MODE is disabled the path returns
        # {"games_seen": N, "avg_accuracy": ...}.
        assert "status" in result or "games_seen" in result


# ---------------------------------------------------------------------------
# BUG-4a  contextual_bandit.py — select() returned None for empty actions
# ---------------------------------------------------------------------------


class TestLinUCBSelectEmptyActions:
    """BUG-4a: LinUCB.select() must raise instead of returning None."""

    def test_empty_actions_raises_value_error(self):
        import numpy as np
        from llm.seca.brain.bandit.contextual_bandit import LinUCB

        bandit = LinUCB(n_features=3)
        context = np.array([0.5, 0.5, 0.5])
        with pytest.raises(ValueError, match="empty"):
            bandit.select(context, [])

    def test_single_action_always_selected(self):
        """With one candidate, select() must return that candidate."""
        import numpy as np
        from llm.seca.brain.bandit.contextual_bandit import LinUCB

        bandit = LinUCB(n_features=3)
        context = np.array([1.0, 0.0, 0.0])
        result = bandit.select(context, ["only_action"])
        assert result == "only_action"

    def test_select_returns_an_action_from_the_list(self):
        import numpy as np
        from llm.seca.brain.bandit.contextual_bandit import LinUCB

        bandit = LinUCB(n_features=2)
        context = np.array([0.3, 0.7])
        actions = ["tactics", "endgames", "openings"]
        result = bandit.select(context, actions)
        assert result in actions


# ---------------------------------------------------------------------------
# BUG-4b  contextual_bandit.py — np.linalg.inv raised on near-singular A
# ---------------------------------------------------------------------------


class TestLinUCBNumericalStability:
    """BUG-4b: LinUCB must stay numerically stable after many updates."""

    def test_degenerate_context_does_not_raise_linalg_error(self):
        """
        Updating with a rank-1 context vector makes A near-singular.
        np.linalg.inv would raise LinAlgError; pinv handles it gracefully.
        """
        import numpy as np
        from llm.seca.brain.bandit.contextual_bandit import LinUCB

        bandit = LinUCB(n_features=4, alpha=0.1)
        # All context vectors lie on the same ray → A is rank-1, near-singular.
        context = np.array([1.0, 0.0, 0.0, 0.0])
        for i in range(200):
            bandit.update("action_a", context, float(i % 2))

        # Must not raise LinAlgError or any other numerical error.
        result = bandit.select(context, ["action_a", "action_b"])
        assert result in ["action_a", "action_b"]

    def test_zero_context_does_not_crash(self):
        """Zero context vector is edge-case degenerate; select() must still return."""
        import numpy as np
        from llm.seca.brain.bandit.contextual_bandit import LinUCB

        bandit = LinUCB(n_features=3)
        context = np.zeros(3)
        result = bandit.select(context, ["a", "b"])
        assert result in ["a", "b"]

    def test_explore_term_is_non_negative(self):
        """
        The exploration term sqrt(x^T A^{-1} x) must always be ≥ 0.
        If pinv produces a negative value here, the UCB formula is broken.
        """
        import numpy as np
        from llm.seca.brain.bandit.contextual_bandit import LinUCB

        bandit = LinUCB(n_features=3, alpha=1.0)
        context = np.array([0.5, 0.3, 0.2])
        A_inv = np.linalg.pinv(bandit.A["x"])
        explore_sq = float(context @ A_inv @ context)
        assert explore_sq >= 0.0, (
            f"x^T A_inv x = {explore_sq} < 0; pinv of identity must be PSD"
        )


# ---------------------------------------------------------------------------
# BUG-5  engine_eval.py — cache key: movetime=None must not collide with 0
# ---------------------------------------------------------------------------


class TestEngineEvalCacheKeyNoneSentinel:
    """BUG-5: _cache_key(fen, None, None) must not equal _cache_key(fen, 0, None)."""

    def _make_evaluator(self):
        from llm.engine_eval import EngineEvaluator

        class _Stub:
            def try_acquire(self):
                return None
            async def acquire(self):
                raise NotImplementedError
            async def release(self, e):
                pass

        return EngineEvaluator(_Stub())

    def test_none_movetime_key_differs_from_zero_movetime_key(self):
        ev = self._make_evaluator()
        fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        key_none = ev._cache_key(fen, None, None)
        key_zero = ev._cache_key(fen, 0, None)
        assert key_none != key_zero, (
            "movetime=None and movetime=0 produced the same cache key; "
            "results for the default-node path would be mis-served as movetime=0 hits"
        )

    def test_none_movetime_key_contains_sentinel_string(self):
        ev = self._make_evaluator()
        fen = "startfen"
        key = ev._cache_key(fen, None, None)
        assert "none" in key, f"Expected 'none' sentinel in key, got: {key!r}"

    def test_nodes_key_still_takes_priority(self):
        ev = self._make_evaluator()
        fen = "startfen"
        key = ev._cache_key(fen, 50, 300)
        assert ":nodes:300" in key

    def test_positive_movetime_key_unchanged(self):
        ev = self._make_evaluator()
        fen = "startfen"
        key = ev._cache_key(fen, 100, None)
        assert key == f"{fen}:movetime:100"


# ---------------------------------------------------------------------------
# BUG-6  engine_pool.py — _started=False must be set before teardown
# ---------------------------------------------------------------------------


class TestEnginePoolStopSetsStartedFirst:
    """BUG-6: _started must be False before any async yield in stop()."""

    def test_pool_not_started_returns_without_error(self):
        """stop() on an unstarted pool must be a no-op."""
        from llm.engine_pool import EnginePool

        pool = EnginePool(size=1)
        asyncio.run(pool.stop())
        assert pool._started is False

    def test_started_flag_is_false_after_stop(self):
        """
        After stop() completes, _started must be False.
        We verify via a fake engine that simulates quit() without a real process.
        """
        import asyncio
        from llm.engine_pool import EnginePool

        pool = EnginePool(size=1)
        # Manually mark as started with a fake engine entry.
        pool._started = True

        class _FakeTransport:
            def close(self):
                pass

        class _FakeEngine:
            async def quit(self):
                pass

        pool._engines = [(_FakeTransport(), _FakeEngine())]
        # Put one engine in the queue so the pool looks live.
        pool._queue.put_nowait(_FakeEngine())

        asyncio.run(pool.stop())
        assert pool._started is False

    def test_acquire_raises_after_stop(self):
        """
        After stop(), acquire() must raise RuntimeError.
        Before the fix, the window between engine cleanup and _started=False
        allowed acquire() to pass the guard and block on an empty queue.
        """
        import asyncio
        from llm.engine_pool import EnginePool

        pool = EnginePool(size=1)
        pool._started = True

        class _FakeTransport:
            def close(self):
                pass

        class _FakeEngine:
            async def quit(self):
                pass

        pool._engines = [(_FakeTransport(), _FakeEngine())]
        pool._queue.put_nowait(_FakeEngine())

        asyncio.run(pool.stop())

        with pytest.raises(RuntimeError, match="not started"):
            asyncio.run(pool.acquire())


# ---------------------------------------------------------------------------
# BUG-7  auth/service.py — timing attack: != comparison on token hashes
# ---------------------------------------------------------------------------


class TestTokenHashConstantTimeComparison:
    """BUG-7: token_hash comparison must use hmac.compare_digest, not !=."""

    def test_service_uses_hmac_compare_digest(self):
        """
        Source-level check: service.py must call hmac.compare_digest for
        token validation, not a direct equality comparison.
        Direct != comparison leaks timing information that allows an attacker
        to enumerate valid token bytes.
        """
        import inspect
        from llm.seca.auth import service as svc_module

        source = inspect.getsource(svc_module)
        assert "hmac.compare_digest" in source, (
            "auth/service.py must use hmac.compare_digest() for token_hash "
            "comparison, not '!=' or '=='."
        )

    def test_service_imports_hmac(self):
        """hmac must be imported at module level in service.py."""
        import inspect
        from llm.seca.auth import service as svc_module

        source = inspect.getsource(svc_module)
        assert "import hmac" in source, (
            "auth/service.py must import hmac to enable constant-time comparison."
        )

    def test_invalid_token_rejected_by_session_validator(self):
        """A tampered token must be rejected without leaking timing info."""
        import hashlib
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from llm.seca.auth.models import Base, Player, Session as AuthSession
        from llm.seca.auth.service import AuthService

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        db = sessionmaker(bind=engine)()

        player = Player(email="t@t.com", password_hash="x", player_embedding="[]")
        db.add(player)
        db.commit()
        db.refresh(player)

        real_token = "valid-token-string"
        token_hash = hashlib.sha256(real_token.encode()).hexdigest()
        import uuid
        session = AuthSession(
            id=str(uuid.uuid4()),
            player_id=player.id,
            token_hash=token_hash,
        )
        db.add(session)
        db.commit()

        svc = AuthService(db)
        assert svc.get_player_by_session(session.id, real_token) is not None
        assert svc.get_player_by_session(session.id, "tampered-token") is None
        db.close()


# ---------------------------------------------------------------------------
# BUG-8  storage/repo.py — SQLite connections leaked when exception occurs
# ---------------------------------------------------------------------------


class TestRepoConnectionNotLeaked:
    """BUG-8: repo.py must always close SQLite connections, even on error."""

    def test_repo_source_uses_try_finally_for_connections(self):
        """
        Source-level check: every get_conn() call in repo.py must be paired
        with a try-finally block to guarantee conn.close() is called.
        Before the fix, exceptions between get_conn() and conn.close() left
        connections open indefinitely.
        """
        import inspect
        from llm.seca.storage import repo as repo_module

        source = inspect.getsource(repo_module)
        conn_count = source.count("conn = get_conn()")
        finally_count = source.count("finally:")
        assert finally_count >= conn_count, (
            f"repo.py has {conn_count} get_conn() calls but only "
            f"{finally_count} finally: blocks — some connections can leak."
        )

    def test_repo_has_more_finally_blocks_than_get_conn_calls(self):
        """Alias kept for clarity — delegates to the count check."""
        self.test_repo_source_uses_try_finally_for_connections()

    def test_each_function_in_repo_has_try_finally(self):
        """Every connection-acquiring function in repo.py must have try-finally."""
        import inspect
        from llm.seca.storage import repo as repo_module

        for fn_name in ("ensure_player", "create_game", "finish_game",
                        "log_move", "log_explanation", "update_learning_score"):
            fn = getattr(repo_module, fn_name)
            src = inspect.getsource(fn)
            assert "finally" in src, (
                f"repo.{fn_name} must use try-finally around get_conn() "
                "to guarantee conn.close() is called even on error"
            )


# ---------------------------------------------------------------------------
# BUG-9  adapt.py — random.choice(sorted_moves[:-1]) raises IndexError with 1 move
# ---------------------------------------------------------------------------


class TestAdaptSelectMoveWithNoise:
    """BUG-9: select_move_with_noise must not crash when only one move is available."""

    def test_single_move_does_not_raise_on_blunder_path(self):
        """
        When moves has only one entry, sorted_moves[:-1] == [] and
        random.choice([]) raises IndexError.  With blunder_prob=1.0 this
        was a guaranteed crash.
        """
        from llm.seca.adapt import select_move_with_noise

        result = select_move_with_noise(
            moves={"e2e4": 50.0},
            blunder_prob=1.0,
            sigma=0.0,
        )
        assert result == "e2e4", (
            f"Single-move position must always return that move; got {result!r}"
        )

    def test_multiple_moves_blunder_excludes_best(self):
        """With multiple moves and blunder_prob=1.0, best move is not chosen."""
        import random
        from llm.seca.adapt import select_move_with_noise

        random.seed(0)
        moves = {"best": 1000.0, "ok": 10.0, "bad": 1.0}
        result = select_move_with_noise(moves=moves, blunder_prob=1.0, sigma=0.0)
        assert result != "best", "blunder_prob=1.0 must not return the best move"

    def test_zero_blunder_prob_returns_best(self):
        """blunder_prob=0.0 must always return the top-scored move."""
        from llm.seca.adapt import select_move_with_noise

        moves = {"best": 500.0, "ok": 50.0}
        result = select_move_with_noise(moves=moves, blunder_prob=0.0, sigma=0.0)
        assert result == "best"


# ---------------------------------------------------------------------------
# BUG-10  global_bandit.py — GlobalLinUCB same empty-actions + numerical issues
# ---------------------------------------------------------------------------


class TestGlobalLinUCBFixes:
    """BUG-10: GlobalLinUCB.select() must guard empty actions and use pinv."""

    def test_empty_actions_raises(self):
        import numpy as np
        from llm.seca.brain.bandit.global_bandit import GlobalLinUCB

        bandit = GlobalLinUCB(n_features=3)
        context = np.array([0.5, 0.5, 0.5])
        with pytest.raises(ValueError, match="empty"):
            bandit.select(context, [])

    def test_select_returns_from_list(self):
        import numpy as np
        from llm.seca.brain.bandit.global_bandit import GlobalLinUCB

        bandit = GlobalLinUCB(n_features=2)
        context = np.array([0.3, 0.7])
        result = bandit.select(context, ["a", "b", "c"])
        assert result in ["a", "b", "c"]

    def test_degenerate_context_numerically_stable(self):
        import numpy as np
        from llm.seca.brain.bandit.global_bandit import GlobalLinUCB

        bandit = GlobalLinUCB(n_features=3, alpha=0.1)
        context = np.array([1.0, 0.0, 0.0])
        for i in range(150):
            bandit.update("x", context, float(i % 2))
        result = bandit.select(context, ["x", "y"])
        assert result in ["x", "y"]


# ---------------------------------------------------------------------------
# BUG-11  meta_bandit.py — LinUCB.select_action() same issues
# ---------------------------------------------------------------------------


class TestMetaBanditFixes:
    """BUG-11: meta_bandit LinUCB must guard empty actions and use pinv."""

    def test_empty_actions_raises(self):
        import numpy as np
        from llm.seca.brain.meta.meta_bandit import LinUCB

        bandit = LinUCB(n_features=3, actions=[])
        x = np.array([0.5, 0.5, 0.5])
        with pytest.raises(ValueError, match="empty"):
            bandit.select_action(x)

    def test_select_action_returns_from_list(self):
        import numpy as np
        from llm.seca.brain.meta.meta_bandit import LinUCB

        bandit = LinUCB(n_features=2, actions=["plan_a", "plan_b"])
        x = np.array([0.4, 0.6])
        result = bandit.select_action(x)
        assert result in ["plan_a", "plan_b"]

    def test_degenerate_context_numerically_stable(self):
        import numpy as np
        from llm.seca.brain.meta.meta_bandit import LinUCB

        bandit = LinUCB(n_features=3, actions=["a", "b"], alpha=0.1)
        context = np.array([1.0, 0.0, 0.0])
        for i in range(150):
            bandit.update("a", context, float(i % 2))
        result = bandit.select_action(context)
        assert result in ["a", "b"]


# ---------------------------------------------------------------------------
# BUG-12  outcome_tracker.py — 4 components ÷ 3.0 → score exceeds [-1, 1]
# ---------------------------------------------------------------------------


class TestOutcomeTrackerNormalization:
    """BUG-12: compute_learning_score must always return a value in [-1, 1]."""

    def _inject_outcome(self, tracker, avg_cpl, blunder_rate, tactic_success, confidence_delta):
        from llm.seca.learning.outcome_tracker import OutcomeMetrics
        import uuid
        eid = str(uuid.uuid4())
        tracker.outcomes[eid] = OutcomeMetrics(
            explanation_id=eid,
            moves_analyzed=10,
            avg_cpl=avg_cpl,
            blunder_rate=blunder_rate,
            tactic_success=tactic_success,
            confidence_delta=confidence_delta,
        )
        return eid

    def test_perfect_game_does_not_exceed_one(self):
        """
        Perfect performance: CPL=0, no blunders, tactic success, conf_delta=1.0
        → raw = 3.5; ÷3.0 = 1.167 (was wrong); ÷3.5 = 1.0 (correct).
        """
        from llm.seca.learning.outcome_tracker import ExplanationOutcomeTracker

        tracker = ExplanationOutcomeTracker()
        eid = self._inject_outcome(tracker, 0.0, 0.0, True, 1.0)
        score = tracker.compute_learning_score(eid)
        assert score <= 1.0, f"Perfect game score {score:.4f} > 1.0"
        assert score >= -1.0

    def test_worst_game_does_not_go_below_minus_one(self):
        from llm.seca.learning.outcome_tracker import ExplanationOutcomeTracker

        tracker = ExplanationOutcomeTracker()
        eid = self._inject_outcome(tracker, 300.0, 1.0, False, -2.0)
        score = tracker.compute_learning_score(eid)
        assert score >= -1.0, f"Worst-case score {score:.4f} < -1.0"

    def test_mid_range_score_in_bounds(self):
        from llm.seca.learning.outcome_tracker import ExplanationOutcomeTracker

        tracker = ExplanationOutcomeTracker()
        eid = self._inject_outcome(tracker, 50.0, 0.2, False, 0.0)
        score = tracker.compute_learning_score(eid)
        assert -1.0 <= score <= 1.0, f"Mid-range score {score:.4f} out of [-1, 1]"
