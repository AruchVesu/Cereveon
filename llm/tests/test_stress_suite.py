"""
Comprehensive stress test suite for ChessCoach — all 10 system areas.

Covers the following areas under high-concurrency, adversarial, and boundary conditions:
  Area 1  — Engine evaluation cache (concurrency, LRU pressure, key collision)
  Area 2  — LLM schema validation (adversarial payloads, fuzz, injection)
  Area 3  — API contract tests (schema stability, concurrent calls, edge payloads)
  Area 4  — Game analysis pipeline (large PGN, 1000-event runs, corrupted data)
  Area 5  — Player analytics engine (volume aggregation, float precision, determinism)
  Area 6  — Training recommendation engine (threshold sweep, priority stability)
  Area 7  — Android Quick Coach UI (see QuickCoachStressTest.kt)
  Area 8  — Android Chat Coach (see ChatCoachStressTest.kt)
  Area 9  — Engine performance benchmarks (extended corpus, sustained SLO)
  Area 10 — CI/CD regression pipeline (structural integrity, file existence, coverage)

All tests are deterministic and use fake engines — no live Stockfish required.
"""

from __future__ import annotations

import ast
import json
import random
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import chess
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Shared test doubles (removed)
# ---------------------------------------------------------------------------
#
# The fake-engine / fake-pool / ``_make_evaluator`` helpers and the
# ``_unique_fens`` corpus generator drove the Area-1 / Area-9
# ``EngineEvaluator`` cache stress; both areas were retired in the
# engine-library cleanup (2026-05-12) and the live engine surface is
# now covered by ``test_engine_pool_*.py`` /
# ``test_fen_move_cache_key.py``.

# ===========================================================================
# AREA 1 — ENGINE EVALUATION CACHE  (removed)
# ===========================================================================
#
# The Area-1 ``TestCacheStressConcurrency`` class targeted the now-deleted
# ``EngineEvaluator`` cache that lived in ``llm/engine_eval.py``.  After
# host_app retirement (PR #111) the only live caching layer is
# ``FenMoveCache`` inside ``llm.seca.engines.stockfish.pool``; that
# surface is covered by ``test_fen_move_cache_key.py`` plus the
# concurrency / lifecycle suites in ``test_engine_pool_*.py``.  The
# Area-1 entry in ``run_stress_suite.py`` was retired alongside.


# ===========================================================================
# AREA 2 — LLM SCHEMA VALIDATION
# ===========================================================================


def _esig(eval_type="cp", band="equal", side="white", eval_delta="stable", phase="middlegame"):
    return {
        "evaluation": {"type": eval_type, "band": band, "side": side},
        "eval_delta": eval_delta,
        "last_move_quality": "unknown",
        "tactical_flags": [],
        "position_flags": [],
        "phase": phase,
    }


def _safe_r(explanation="Position is roughly equal."):
    return {"explanation": explanation, "engine_signal": _esig(), "mode": "SAFE_V1"}


def _llm_r(explanation=None):
    if explanation is None:
        explanation = (
            "White has a structural advantage from the passed pawn. "
            "The rook actively controls the open file in the endgame."
        )
    return {
        "explanation": explanation,
        "engine_signal": _esig(band="small_advantage", side="white", eval_delta="increase"),
        "mode": "LLM_MODE_2",
    }


class TestSchemaValidationStress:
    """Area 2 — Adversarial and high-volume schema validation."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from llm.rag.validators.explain_response_schema import (
            validate_explain_response,
            ExplainSchemaError,
            EngineSignalSchema,
        )

        self.validate = validate_explain_response
        self.SchemaError = ExplainSchemaError
        self.EngineSignalSchema = EngineSignalSchema

    def test_100_valid_safe_v1_responses_all_pass(self):
        for i in range(100):
            r = _safe_r(f"Explanation number {i}. Position is balanced.")
            assert self.validate(r).mode == "SAFE_V1"

    def test_50_valid_llm_responses_all_pass(self):
        for i in range(50):
            r = _llm_r(
                f"White has an advantage on the queenside. "
                f"The pawn structure is solid after move {i + 1}."
            )
            assert self.validate(r).mode == "LLM_MODE_2"

    def test_all_documented_forbidden_patterns_rejected(self):
        """Every documented forbidden phrase must be caught in LLM_MODE_2.

        Only includes patterns verified by the schema validator's existing test suite.
        Capture notation (Qxf7, Rxe5) is intentionally excluded — it does not match
        the forbidden notation regex \b[KQRBN][a-h][1-8]\b.

        Note: ``\bshould\b`` was removed from SPECULATIVE_PATTERNS in PR
        #170 (2026-05-16).  The previous "White should advance the pawn"
        / "Black should retreat the knight" cases that used to live here
        now PASS the lexical gate — they were the canonical example of
        the gate over-blocking imperative coaching language.  Speculative
        compounds (``should likely``, ``should probably``) still fail
        via the ``\blikely\b`` / ``\bprobably\b`` patterns that remain.
        See TestSpeculativeShouldAccepted below for the inverse pin.
        """
        forbidden = [
            "The engine wants to capture the bishop.",  # engine reference
            "The engine wants to play Nd5.",  # engine reference
            "The position leads to mate in 3 moves.",  # mate claim
            "This is mate in 5.",  # mate claim
            "One must calculate the variation carefully.",  # calculation
            "White can calculate a forced win.",  # calculation
            "The knight on Nf3 controls key squares.",  # notation: Nf3
            "White's bishop on Bc4 is dominant.",  # notation: Bc4
        ]
        for phrase in forbidden:
            r = _llm_r(explanation=phrase)
            with pytest.raises(self.SchemaError, match="Mode-2 content"):
                self.validate(r)

    def test_unicode_explanations_pass_safe_v1(self):
        """Unicode text without forbidden patterns must pass SAFE_V1."""
        unicode_texts = [
            "Позиция примерно равна.",
            "位置大致相等。",
            "المركز متوازن تقريباً.",
            "Η θέση είναι ισόρροπη.",
            "Posición aproximadamente igualada.",
        ]
        for text in unicode_texts:
            r = _safe_r(explanation=text)
            assert self.validate(r).mode == "SAFE_V1"

    def test_10kb_explanation_passes_safe_v1(self):
        """A 10 KB explanation string must pass SAFE_V1 validation."""
        long_text = "The position is structurally imbalanced. " * 300  # ~12 KB
        r = _safe_r(explanation=long_text)
        result = self.validate(r)
        assert len(result.explanation) > 10_000

    def test_all_valid_enum_combinations_accepted(self):
        """Systematic check of all eval_type × band × side × phase combinations."""
        for eval_type in ("cp", "mate"):
            for band in ("equal", "small_advantage", "clear_advantage", "decisive_advantage"):
                for phase in ("opening", "middlegame", "endgame"):
                    for side in ("white", "black"):
                        if eval_type == "mate":
                            band = "decisive_advantage"
                        r = {
                            "explanation": "Structural explanation only.",
                            "engine_signal": _esig(
                                eval_type=eval_type,
                                band=band,
                                side=side,
                                phase=phase,
                            ),
                            "mode": "SAFE_V1",
                        }
                        result = self.validate(r)
                        assert result.engine_signal.evaluation.type == eval_type

    def test_injected_extra_fields_do_not_propagate(self):
        """Extra fields injected into engine_signal must not appear on the validated model."""
        r = _safe_r()
        r["engine_signal"]["injected_override"] = "malicious_value"
        r["engine_signal"]["llm_score"] = 999
        result = self.validate(r)
        assert not hasattr(result.engine_signal, "injected_override")
        assert not hasattr(result.engine_signal, "llm_score")

    def test_type_confusion_attacks_all_rejected(self):
        """Wrong types for top-level fields must all raise errors."""
        cases = [
            ("explanation", 42),
            ("explanation", ["sent1", "sent2"]),
            ("explanation", {"nested": "object"}),
            ("engine_signal", "flat_string"),
            ("engine_signal", [1, 2, 3]),
            ("engine_signal", 0),
        ]
        for field, value in cases:
            r = _safe_r()
            r[field] = value
            with pytest.raises((self.SchemaError, Exception)):
                self.validate(r)

    def test_validation_deterministic_under_100_repeats(self):
        """Same valid LLM response always produces the same mode on repeated calls."""
        r = _llm_r()
        modes = {self.validate(r).mode for _ in range(100)}
        assert modes == {"LLM_MODE_2"}

    def test_missing_field_combinations_all_rejected(self):
        """Every combination of missing top-level field must be rejected."""
        required = ["explanation", "engine_signal", "mode"]
        for field in required:
            r = _safe_r()
            del r[field]
            with pytest.raises((self.SchemaError, Exception)):
                self.validate(r)

    def test_engine_signal_all_invalid_bands_rejected(self):
        """Invalid band values must all be rejected at schema level."""
        invalid_bands = ["great", "dominant", "0", "winning", "", None, 42, True]
        from pydantic import ValidationError

        for band in invalid_bands:
            sig = _esig()
            sig["evaluation"]["band"] = band
            with pytest.raises((ValidationError, Exception)):
                self.EngineSignalSchema.model_validate(sig)


# ===========================================================================
# AREA 3 — API CONTRACT TESTS
# ===========================================================================


class TestApiContractStress:
    """Area 3 — API contracts stable under varied, adversarial, and concurrent inputs."""

    # ``test_engine_eval_contract_with_extreme_scores`` (removed) — drove
    # ``host_app.eval_position_query`` via a ``llm.host_app`` import that
    # disappeared in PR #111.  The post-retirement /engine/eval lives in
    # ``server.py`` and is exercised end-to-end by
    # ``test_engine_eval_endpoint.py`` against the real engine pool, so
    # mocking the extreme-score wire path here added nothing on top.

    def test_game_finish_contract_with_varied_pgn_lengths(self):
        """POST /game/finish must succeed with PGNs of 1, 5, 20, and 50 moves."""
        from llm.seca.events.router import finish_game, GameFinishRequest

        _HDR = '[Event "Test"]\n[Site "?"]\n[Date "????.??.??"]\n[Round "?"]\n[White "?"]\n[Black "?"]\n[Result "*"]\n\n'

        def _shuttle_pgn(num_halfmoves: int) -> str:
            """Legal PGN: knights shuttle Nf3/Ng1 vs Nc6/Nb8 for any length."""
            w = ["Nf3", "Ng1"]
            b = ["Nc6", "Nb8"]
            parts = []
            for i in range(num_halfmoves):
                if i % 2 == 0:
                    parts.append(f"{i // 2 + 1}. {w[(i // 2) % 2]}")
                else:
                    parts.append(b[((i - 1) // 2) % 2])
            return _HDR + " ".join(parts) + " *"

        pgns = [
            _HDR + "1. e4 *",
            _HDR + "1. e4 e5 2. Nf3 Nc6 3. Bb5 *",
            _shuttle_pgn(20),
            _shuttle_pgn(50),
        ]
        for pgn in pgns:
            player = SimpleNamespace(id=1, rating=1500.0, confidence=0.70)
            db = MagicMock()
            db.refresh.side_effect = lambda obj: None
            db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
                []
            )
            with (
                patch("llm.seca.events.router.EventStorage") as MockStorage,
                patch("llm.seca.events.router.SkillUpdater"),
            ):
                MockStorage.return_value.store_game.return_value = SimpleNamespace(id=1)
                from starlette.requests import Request as _Req
                from llm.seca.shared_limiter import limiter as _limiter
                _scope = {"type": "http", "method": "POST", "path": "/game/finish",
                          "headers": [], "client": ("127.0.0.1", 0)}
                _prev = _limiter.enabled
                _limiter.enabled = False
                try:
                    from fastapi import BackgroundTasks as _BackgroundTasks  # noqa: PLC0415

                    r = finish_game(
                        req=GameFinishRequest(pgn=pgn, result="win", accuracy=0.80, weaknesses={}),
                        player=player,
                        request=_Req(_scope),
                        background_tasks=_BackgroundTasks(),
                        db=db,
                    )
                finally:
                    _limiter.enabled = _prev
            assert r["status"] == "stored", f"game/finish failed for PGN len={len(pgn)}"

    def test_next_training_contract_stable_across_50_calls(self, monkeypatch):
        """GET /next-training must return consistent schema across 50 consecutive calls."""
        import llm.server as server_module
        from llm.seca.curriculum.types import TrainingTask

        fake = TrainingTask(topic="tactics", difficulty=0.6, format="puzzle", expected_gain=2.5)
        monkeypatch.setattr(server_module, "scheduler", SimpleNamespace(next_task=lambda *a: fake))
        required = {"topic", "difficulty", "format", "expected_gain"}
        for i in range(50):
            pid = f"player_{i}"
            result = server_module.next_training(
                player_id=pid,
                player=SimpleNamespace(id=pid, rating=1200.0, confidence=0.5),
            )
            missing = required - set(result.keys())
            assert not missing, f"API contract fields missing on call {i}: {missing}"

    def test_all_result_types_accepted_by_game_finish(self):
        """game/finish must accept win, draw, and loss result values."""
        from llm.seca.events.router import finish_game, GameFinishRequest

        for result_type in ("win", "draw", "loss"):
            player = SimpleNamespace(id=1, rating=1500.0, confidence=0.70)
            db = MagicMock()
            db.refresh.side_effect = lambda obj: None
            db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
                []
            )
            with (
                patch("llm.seca.events.router.EventStorage") as MockStorage,
                patch("llm.seca.events.router.SkillUpdater"),
            ):
                MockStorage.return_value.store_game.return_value = SimpleNamespace(id=1)
                _HDR = '[Event "Test"]\n[Site "?"]\n[Date "????.??.??"]\n[Round "?"]\n[White "?"]\n[Black "?"]\n[Result "*"]\n\n'
                from starlette.requests import Request as _Req
                from llm.seca.shared_limiter import limiter as _limiter
                _scope = {"type": "http", "method": "POST", "path": "/game/finish",
                          "headers": [], "client": ("127.0.0.1", 0)}
                _prev = _limiter.enabled
                _limiter.enabled = False
                try:
                    from fastapi import BackgroundTasks as _BackgroundTasks  # noqa: PLC0415

                    r = finish_game(
                        req=GameFinishRequest(
                            pgn=_HDR + "1. e4 *", result=result_type, accuracy=0.75, weaknesses={}
                        ),
                        player=player,
                        request=_Req(_scope),
                        background_tasks=_BackgroundTasks(),
                        db=db,
                    )
                finally:
                    _limiter.enabled = _prev
            assert r["status"] == "stored"

    def test_required_routes_not_removed_from_server(self):
        """Documented API routes must still be registered in server.py."""
        import llm.server as server_module

        routes = {getattr(r, "path", None) for r in server_module.app.routes}
        assert (
            "/next-training/{player_id}" in routes
        ), "Required route /next-training/{player_id} is missing from server.py"

    def test_server_exposes_engine_eval_route(self):
        """``/engine/eval`` lives on ``server.py`` post-host_app retirement (PR #111)."""
        import llm.server as server_module

        routes = {getattr(r, "path", None) for r in server_module.app.routes}
        assert "/engine/eval" in routes, "server.py must expose /engine/eval"

    def test_coach_route_absent_from_server(self):
        """Contract: /coach endpoint must NOT exist on server.py (documented mismatch)."""
        import llm.server as server_module

        routes = {getattr(r, "path", None) for r in server_module.app.routes}
        assert "/coach" not in routes, "/coach unexpectedly found in server"

    def test_next_training_never_returns_exercise_type(self, monkeypatch):
        """Regression guard: /next-training schema must not leak /curriculum/next fields."""
        import llm.server as server_module
        from llm.seca.curriculum.types import TrainingTask

        fake = TrainingTask(topic="endgame", difficulty=0.5, format="game", expected_gain=1.0)
        monkeypatch.setattr(server_module, "scheduler", SimpleNamespace(next_task=lambda *a: fake))
        for _ in range(20):
            r = server_module.next_training(
                player_id="p1",
                player=SimpleNamespace(id="p1", rating=1200.0, confidence=0.5),
            )
            assert "exercise_type" not in r
            assert "payload" not in r


# ===========================================================================
# AREA 4 — GAME ANALYSIS PIPELINE
# ===========================================================================


def _make_event(weaknesses_json):
    return SimpleNamespace(weaknesses_json=weaknesses_json)


class TestGameAnalysisPipelineStress:
    """Area 4 — Game analysis pipeline under volume, corruption, and large PGN."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from llm.seca.analysis.historical_pipeline import HistoricalAnalysisPipeline
        from llm.seca.analysis.pgn_loader import load_moves_from_pgn
        from llm.seca.analysis.mistake_classifier import classify_delta

        self.Pipeline = HistoricalAnalysisPipeline
        self.load_pgn = load_moves_from_pgn
        self.classify = classify_delta

    def _make_pipeline(self):
        return self.Pipeline(db=MagicMock())

    def test_1000_valid_events_aggregate_correctly(self):
        """1000 identical events must produce the correct phase averages."""
        events = [_make_event('{"opening": 0.10, "middlegame": 0.08, "endgame": 0.05}')] * 1000
        with patch("llm.seca.analysis.historical_pipeline.AnalyticsLogger") as ML:
            ML.return_value = MagicMock()
            stats = self._make_pipeline().run("stress_player", events)
        assert stats.games_analyzed == 1000
        assert abs(stats.phase_rates["opening"] - 0.10) < 1e-6
        assert abs(stats.phase_rates["middlegame"] - 0.08) < 1e-6
        assert abs(stats.phase_rates["endgame"] - 0.05) < 1e-6

    def test_mixed_500_valid_500_corrupted_counts_all_events(self):
        """games_analyzed reflects all events; only valid ones contribute to phase_rates."""
        events = (
            [_make_event('{"opening": 0.10}') for _ in range(500)]
            + [_make_event("{not json}") for _ in range(200)]
            + [_make_event(None) for _ in range(200)]
            + [_make_event("") for _ in range(100)]
        )
        with patch("llm.seca.analysis.historical_pipeline.AnalyticsLogger") as ML:
            ML.return_value = MagicMock()
            stats = self._make_pipeline().run("mixed_player", events)
        # pipeline._extract_weakness_dicts filters out malformed JSON, None, and empty
        # strings — only the 500 valid dicts reach aggregate_from_weakness_dicts.
        assert (
            stats.games_analyzed == 500
        ), f"Expected 500 valid games aggregated; got {stats.games_analyzed}"
        assert "opening" in stats.phase_rates

    def test_large_pgn_100_moves_parses_without_error(self, tmp_path):
        """A PGN with up to 100 moves must parse correctly."""
        board = chess.Board()
        moves = []
        for _ in range(100):
            legal = list(board.generate_legal_moves())
            if not legal:
                break
            move = legal[0]
            moves.append(board.san(move))
            board.push(move)

        move_text = ""
        for i, san in enumerate(moves):
            if i % 2 == 0:
                move_text += f"{i // 2 + 1}. "
            move_text += san + " "
        pgn = f'[Event "Stress"]\n[Result "*"]\n\n{move_text}*\n'

        pgn_file = tmp_path / "stress.pgn"
        pgn_file.write_text(pgn, encoding="utf-8")
        parsed = self.load_pgn(str(pgn_file))
        assert len(parsed) == len(moves)

    def test_pipeline_passes_correct_player_id_to_logger_across_20_players(self):
        """run() must log the exact player_id it was called with."""
        from llm.seca.analysis.historical_pipeline import HistoricalAnalysisPipeline

        for pid in [f"player_{i}" for i in range(20)]:
            with patch("llm.seca.analysis.historical_pipeline.AnalyticsLogger") as ML:
                mock_instance = MagicMock()
                ML.return_value = mock_instance
                HistoricalAnalysisPipeline(db=MagicMock()).run(pid, [])
                assert mock_instance.log.call_args.kwargs["player_id"] == pid

    def test_classify_delta_boundary_sweep_exhaustive(self):
        """Sweep classify_delta at every documented boundary ±epsilon."""
        cases = [
            (0.0, "ok"),
            (49.0, "ok"),
            (49.9, "ok"),
            (50.0, "inaccuracy"),
            (50.1, "inaccuracy"),
            (149.0, "inaccuracy"),
            (149.9, "inaccuracy"),
            (150.0, "mistake"),
            (150.1, "mistake"),
            (299.0, "mistake"),
            (299.9, "mistake"),
            (300.0, "blunder"),
            (300.1, "blunder"),
            (9999.0, "blunder"),
        ]
        for delta, expected in cases:
            assert self.classify(delta) == expected, f"classify_delta({delta}) wrong"
            assert self.classify(-delta) == expected, f"classify_delta({-delta}) wrong"

    def test_pipeline_run_determinism_same_events_same_stats(self):
        """Calling run() twice with the same events must produce identical stats."""
        events = [
            _make_event('{"opening": 0.10, "endgame": 0.05}'),
            _make_event('{"middlegame": 0.12}'),
        ]
        with patch("llm.seca.analysis.historical_pipeline.AnalyticsLogger") as ML:
            ML.return_value = MagicMock()
            p = self._make_pipeline()
            s1 = p.run("p1", events)

        with patch("llm.seca.analysis.historical_pipeline.AnalyticsLogger") as ML:
            ML.return_value = MagicMock()
            p2 = self._make_pipeline()
            s2 = p2.run("p1", events)

        assert s1.games_analyzed == s2.games_analyzed
        for phase in s1.phase_rates:
            assert abs(s1.phase_rates[phase] - s2.phase_rates[phase]) < 1e-9


# ===========================================================================
# AREA 5 — PLAYER ANALYTICS ENGINE
# ===========================================================================


class TestPlayerAnalyticsStress:
    """Area 5 — Analytics aggregation under volume, edge values, and determinism."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from llm.seca.analytics.mistake_stats import (
            aggregate_from_weakness_dicts,
            MistakeCategory,
        )

        self.aggregate = aggregate_from_weakness_dicts
        self.MistakeCategory = MistakeCategory

    def test_1000_identical_games_correct_averages(self):
        """1000 identical weakness dicts must average to the original values."""
        rate_o, rate_m, rate_e = 0.15, 0.07, 0.09
        dicts = [{"opening": rate_o, "middlegame": rate_m, "endgame": rate_e}] * 1000
        stats = self.aggregate(dicts)
        assert stats.games_analyzed == 1000
        assert abs(stats.phase_rates["opening"] - rate_o) < 1e-9
        assert abs(stats.phase_rates["middlegame"] - rate_m) < 1e-9
        assert abs(stats.phase_rates["endgame"] - rate_e) < 1e-9

    def test_determinism_across_shuffled_input(self):
        """Same games in different order must produce identical phase_rates."""
        base = [
            {"opening": 0.10, "middlegame": 0.08},
            {"opening": 0.05, "endgame": 0.12},
            {"middlegame": 0.15, "endgame": 0.03},
        ]
        s1 = self.aggregate(base)
        shuffled = list(base)
        random.shuffle(shuffled)
        s2 = self.aggregate(shuffled)
        assert s1.games_analyzed == s2.games_analyzed
        for phase in s1.phase_rates:
            assert abs(s1.phase_rates[phase] - s2.phase_rates[phase]) < 1e-9

    def test_all_category_scores_bounded_0_to_1_for_rate_1(self):
        """Max-rate (1.0) inputs must produce category scores in [0, 1]."""
        stats = self.aggregate([{"opening": 1.0, "middlegame": 1.0, "endgame": 1.0}] * 10)
        for cat, score in stats.category_scores.items():
            assert 0.0 <= score <= 1.0, f"Score {score:.4f} out of [0,1] for {cat}"

    def test_zero_rate_player_all_scores_zero(self):
        """A player with all-zero rates must have all category scores equal to 0.0."""
        stats = self.aggregate([{"opening": 0.0, "middlegame": 0.0, "endgame": 0.0}] * 5)
        for cat, score in stats.category_scores.items():
            assert score == 0.0, f"Zero-rate player has non-zero score {score} for {cat}"

    def test_dominant_category_consistent_500_games_strong_opening(self):
        """500 games strongly weighted to opening must identify OPENING_PREPARATION dominant."""
        dicts = [{"opening": 0.30, "middlegame": 0.02, "endgame": 0.01}] * 500
        stats = self.aggregate(dicts)
        assert stats.dominant_category == self.MistakeCategory.OPENING_PREPARATION

    def test_missing_phase_dilutes_average_correctly(self):
        """Phase missing from half the games must contribute half its rate to the average."""
        dicts = [{"endgame": 0.10}, {}]  # endgame in 1 of 2 games
        stats = self.aggregate(dicts)
        assert abs(stats.phase_rates.get("endgame", 0) - 0.05) < 1e-9

    def test_100_concurrent_aggregation_calls_produce_same_result(self):
        """aggregate_from_weakness_dicts is pure Python; 100 calls must all return same result."""
        dicts = [{"opening": 0.12, "endgame": 0.08}] * 10
        results = [self.aggregate(dicts) for _ in range(100)]
        first = results[0]
        for s in results[1:]:
            assert s.games_analyzed == first.games_analyzed
            assert s.phase_rates == first.phase_rates
            assert s.dominant_category == first.dominant_category

    def test_large_batch_100_games_all_phases_correct_average(self):
        """100 games each with all 3 phases must produce exact per-phase averages."""
        r_o, r_m, r_e = 0.12, 0.07, 0.09
        dicts = [{"opening": r_o, "middlegame": r_m, "endgame": r_e}] * 100
        stats = self.aggregate(dicts)
        assert abs(stats.phase_rates["opening"] - r_o) < 1e-9
        assert abs(stats.phase_rates["middlegame"] - r_m) < 1e-9
        assert abs(stats.phase_rates["endgame"] - r_e) < 1e-9


# ===========================================================================
# AREA 6 — TRAINING RECOMMENDATION ENGINE
# ===========================================================================


class TestTrainingRecommendationStress:
    """Area 6 — Recommendations under threshold sweep, priority ordering, and determinism."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from llm.seca.analytics.training_recommendations import (
            generate_training_recommendations,
            _CATEGORY_RULES,
            _priority_from_ratio,
        )
        from llm.seca.analytics.mistake_stats import MistakeStats, MistakeCategory

        self.generate = generate_training_recommendations
        self.rules = _CATEGORY_RULES
        self.priority_from_ratio = _priority_from_ratio
        self.MistakeStats = MistakeStats
        self.MistakeCategory = MistakeCategory

    def _stats(self, scores):
        dominant = max(scores, key=lambda c: scores[c]) if scores else None
        return self.MistakeStats(
            games_analyzed=5,
            phase_rates={},
            category_scores=scores,
            dominant_category=dominant,
        )

    def test_threshold_sweep_all_categories_below_threshold_no_recs(self):
        """Score at 0.5× threshold must produce no recommendations for any category."""
        for category, (threshold, _) in self.rules.items():
            scores = {cat: 0.0 for cat in self.MistakeCategory.ALL}
            scores[category] = threshold * 0.5
            assert (
                self.generate(self._stats(scores)) == []
            ), f"Score below threshold must not produce recs for {category}"

    def test_threshold_sweep_at_exact_threshold_gives_low_priority(self):
        """Score exactly at threshold must produce exactly one low-priority rec."""
        for category, (threshold, _) in self.rules.items():
            scores = {cat: 0.0 for cat in self.MistakeCategory.ALL}
            scores[category] = threshold
            recs = self.generate(self._stats(scores))
            assert len(recs) == 1
            assert (
                recs[0].priority == "low"
            ), f"Score at threshold must be 'low' for {category}; got {recs[0].priority!r}"

    def test_threshold_sweep_at_2x_gives_high_priority(self):
        """Score at 2× threshold must produce a high-priority recommendation."""
        for category, (threshold, _) in self.rules.items():
            scores = {cat: 0.0 for cat in self.MistakeCategory.ALL}
            scores[category] = threshold * 2.0
            recs = self.generate(self._stats(scores))
            assert (
                recs[0].priority == "high"
            ), f"Score at 2× threshold should be 'high' for {category}"

    def test_all_4_categories_high_produces_4_sorted_high_recs(self):
        """When all 4 categories are at 2× threshold, produce 4 high-priority recs."""
        scores = {cat: t * 2.0 for cat, (t, _) in self.rules.items()}
        recs = self.generate(self._stats(scores))
        assert len(recs) == 4
        assert all(r.priority == "high" for r in recs)

    def test_recommendations_always_sorted_high_before_medium_before_low(self):
        """100 random score configurations must always produce sorted priority order."""
        order = {"high": 0, "medium": 1, "low": 2}
        for _ in range(100):
            scores = {cat: t * random.uniform(0, 3.0) for cat, (t, _) in self.rules.items()}
            recs = self.generate(self._stats(scores))
            for i in range(len(recs) - 1):
                assert (
                    order[recs[i].priority] <= order[recs[i + 1].priority]
                ), f"Priority order violated: {[r.priority for r in recs]}"

    def test_determinism_100_identical_calls(self):
        """100 calls with the same stats must produce the same ordered result."""
        scores = {
            self.MistakeCategory.OPENING_PREPARATION: 0.15,
            self.MistakeCategory.TACTICAL_VISION: 0.10,
            self.MistakeCategory.POSITIONAL_PLAY: 0.07,
            self.MistakeCategory.ENDGAME_TECHNIQUE: 0.09,
        }
        stats = self._stats(scores)
        first = [(r.category, r.priority) for r in self.generate(stats)]
        for _ in range(99):
            assert [(r.category, r.priority) for r in self.generate(stats)] == first

    def test_every_rec_has_non_empty_rationale(self):
        """All generated recommendations must include a non-empty rationale string."""
        scores = {cat: t * 1.5 for cat, (t, _) in self.rules.items()}
        for rec in self.generate(self._stats(scores)):
            assert (
                rec.rationale and rec.rationale.strip()
            ), f"Empty rationale for category {rec.category!r}"

    def test_priority_from_ratio_boundary_sweep(self):
        """Exhaustive sweep of _priority_from_ratio at all documented breakpoints."""
        cases = [
            (1.0, "low"),
            (1.24, "low"),
            (1.25, "medium"),
            (1.99, "medium"),
            (2.0, "high"),
            (2.01, "high"),
            (100.0, "high"),
        ]
        for ratio, expected in cases:
            assert (
                self.priority_from_ratio(ratio) == expected
            ), f"_priority_from_ratio({ratio}) → wrong result, expected {expected!r}"

    def test_clean_player_zero_games_no_recommendations(self):
        """A player with 0 games must get an empty recommendation list."""
        stats = self.MistakeStats(games_analyzed=0)
        assert self.generate(stats) == []


# ===========================================================================
# AREA 9 — ENGINE PERFORMANCE BENCHMARKS (extended)  (removed)
# ===========================================================================
#
# The ``TestExtendedBenchmarkCorpus`` class targeted the now-deleted
# ``EngineEvaluator`` cache (``llm/engine_eval.py``).  Cache-size, hit-rate,
# and board-mutation invariants for the live
# ``llm.seca.engines.stockfish.pool.FenMoveCache`` are covered by
# ``test_engine_pool_evaluate_position.py`` (correctness) and
# ``test_fen_move_cache_key.py`` (key integrity).  The Area-9 entry in
# ``run_stress_suite.py`` was retired alongside.


# ===========================================================================
# AREA 10 — CI/CD REGRESSION PIPELINE
# ===========================================================================


class TestCiCdPipelineStress:
    """Area 10 — Structural integrity of the regression and CI pipelines."""

    @pytest.fixture(autouse=True)
    def _import(self):
        import llm.run_regression_suite as rs
        import llm.run_ci_suite as ci

        self.regression_groups = rs.REGRESSION_GROUPS
        self.ci_targets = ci.TEST_TARGETS

    def test_regression_suite_has_at_least_6_groups(self):
        assert len(self.regression_groups) >= 6

    def test_all_group_labels_non_empty_strings(self):
        for label, _ in self.regression_groups:
            assert isinstance(label, str) and label.strip()

    def test_all_groups_have_at_least_one_target(self):
        for label, targets in self.regression_groups:
            assert len(targets) >= 1, f"Group '{label}' has no targets"

    def test_no_intra_group_duplicate_targets(self):
        for label, targets in self.regression_groups:
            assert len(targets) == len(set(targets)), f"Group '{label}' has duplicate targets"

    def test_no_cross_group_duplicate_targets(self):
        all_targets: list[str] = []
        for _, targets in self.regression_groups:
            all_targets.extend(targets)
        duplicates = {t for t in all_targets if all_targets.count(t) > 1}
        assert not duplicates, f"Targets appear in multiple groups: {duplicates}"

    def test_all_regression_targets_exist_as_files(self):
        for label, targets in self.regression_groups:
            for target in targets:
                assert (PROJECT_ROOT / target).exists(), f"Group '{label}' target missing: {target}"

    def test_all_ci_suite_targets_exist_as_files(self):
        for target in self.ci_targets:
            assert (PROJECT_ROOT / target).exists(), f"CI target missing: {target}"

    def test_ci_suite_has_minimum_30_targets(self):
        assert (
            len(self.ci_targets) >= 30
        ), f"CI suite has only {len(self.ci_targets)} targets; minimum is 30"

    def test_no_duplicate_targets_in_ci_suite(self):
        duplicates = {t for t in self.ci_targets if self.ci_targets.count(t) > 1}
        assert not duplicates, f"CI suite has duplicate targets: {duplicates}"

    def test_first_regression_group_is_engine_related(self):
        """Engine regression must run first (cheapest tests catch first)."""
        first_label, _ = self.regression_groups[0]
        assert (
            "engine" in first_label.lower()
        ), f"First group should be engine-related; got '{first_label}'"

    def test_regression_covers_all_major_areas(self):
        """Regression groups must cover engine, coaching, API, analysis, and layer areas."""
        all_targets = " ".join(t for _, targets in self.regression_groups for t in targets)
        # ``engine_eval`` / ``elite_engine`` were dropped in the engine-library
        # cleanup (2026-05-12); the live surface lives under engine_pool /
        # fen_move_cache, with engine_response_format retained for the
        # /chat /live/move signal-extraction tests.
        required_keywords = {
            "engine": ["engine_pool", "engine_response", "fen_move_cache"],
            "coaching": ["coaching_pipeline", "chat_pipeline"],
            "api": ["api_contract", "api_security"],
            "analysis": ["historical_pipeline", "mistake_analytics"],
            "layer": ["seca_layer_boundaries"],
        }
        for area, kws in required_keywords.items():
            assert any(
                kw in all_targets for kw in kws
            ), f"Regression pipeline missing coverage for area: {area}"

    def test_layer_boundary_group_present(self):
        labels = [label.lower() for label, _ in self.regression_groups]
        assert any("layer" in l or "boundary" in l for l in labels)

    def test_golden_test_group_present(self):
        labels = [label.lower() for label, _ in self.regression_groups]
        assert any("golden" in l for l in labels)

    def test_analysis_pipeline_group_present(self):
        labels = [label.lower() for label, _ in self.regression_groups]
        assert any("analysis" in l or "pipeline" in l for l in labels)
