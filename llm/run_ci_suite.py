"""Run the stable CI pytest suite with coverage and artifacts."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = PROJECT_ROOT / "tmp_logs"

TEST_TARGETS = [
    "llm/rag/tests/golden/test_retriever.py",
    "llm/rag/tests/golden/test_prompt_snapshot.py",
    "llm/rag/tests/contracts/test_fake_llm.py",
    "llm/rag/tests/contracts/test_mode_2_output.py",
    "llm/rag/tests/contracts/test_violations_corpus.py",
    "llm/rag/tests/test_output_firewall.py",
    "llm/rag/tests/test_run_mode_2_additional.py",
    "llm/rag/tests/test_run_mode_2_cascades.py",
    "llm/rag/tests/test_run_mode_2_mate_sanitization.py",
    "llm/rag/tests/test_explanation_score.py",
    "llm/rag/tests/unit/test_input_sanitizer.py",
    "llm/tests/test_engine_response_format.py",
    "llm/rag/tests/unit/test_telemetry_event.py",
    "llm/tests/test_cache_keys.py",
    "llm/tests/test_ci_pipeline.py",
    "llm/tests/test_elite_engine_service.py",
    "llm/tests/test_elite_engine_service_resolve_limits.py",
    "llm/tests/test_engine_eval_fallback_cache.py",
    "llm/tests/test_engine_eval_lru_cache.py",
    "llm/tests/test_engine_eval_limits.py",
    "llm/tests/test_fen_move_cache_key.py",
    "llm/tests/test_host_app.py",
    "llm/tests/test_position_input_build_board.py",
    "llm/tests/test_predictive_cache.py",
    "llm/tests/test_stockfish_adapter_isolation.py",
    "llm/tests/test_seca_layer_boundaries.py",
    "llm/tests/test_coaching_pipeline_regression.py",
    "llm/tests/test_api_contract_validation.py",
    "llm/tests/test_explain_schema_validation.py",
    "llm/tests/test_mistake_analytics.py",
    "llm/tests/test_chat_pipeline.py",
    "llm/tests/test_chat_stream.py",
    "llm/tests/test_live_move_pipeline.py",
    "llm/tests/test_historical_pipeline.py",
    "llm/tests/test_engine_eval_benchmark.py",
    "llm/tests/test_api_security.py",
    "llm/tests/test_skill_updater_resilience.py",
    "llm/tests/test_next_training_after_game.py",
    "llm/tests/test_engine_eval_android_contract.py",
    "llm/tests/test_game_finish_db_integration.py",
    "llm/tests/test_engine_pool_exhaustion.py",
    "llm/tests/test_cache_redis_unavailable.py",
    "llm/tests/test_seca_status.py",
    "llm/tests/test_full_loop_integration.py",
    "llm/tests/test_curriculum_next_contract.py",
    "llm/tests/test_security_hardening.py",
    # Player adaptation regression suite (full-scale adaptation: /move session auth,
    # curriculum history-driven topic, game finish recommendations, SAFE_MODE gate).
    "llm/tests/test_adaptive_player.py",
    # Auth service and token contract tests (layer-correct: service raises ValueError,
    # router converts to HTTP; JWT creation/expiry/tamper coverage).
    "llm/tests/test_auth_service.py",
    "llm/tests/test_auth_tokens.py",
    # Progress dashboard: /player/progress endpoint contract + world-model transparency tests.
    "llm/tests/test_progress_dashboard.py",
    # Bug regression suite: guards the six confirmed fixes (reward ZeroDivision,
    # spacing zero-interval, trainer empty-events, bandit empty-actions + singular matrix,
    # engine_eval cache-key sentinel, engine_pool stop race).
    "llm/tests/test_bug_regressions.py",
    # API05 — LLM retry cap, inter-retry backoff delay, and safe fallback contract.
    "llm/tests/test_explain_pipeline_retry.py",
    # QA plan — server-related categories (all 111 pass, zero external deps).
    # INV-01–05: architectural invariants (LLM output, engine format, isolation, call order, game independence).
    "llm/tests/test_architectural_invariants.py",
    # SF-01–04: ESV output schema, centipawn coarsening, malformed-input handling, determinism.
    "llm/tests/test_esv_pipeline_qa.py",
    # RAG-01–04: retrieval determinism, relevance, empty-store safety, document immutability.
    "llm/tests/test_rag_pipeline_qa.py",
    # API-01, API-03, API-06, CI-06: health endpoint, invalid-FEN rejection, seca_doctor, deploy chain.
    "llm/tests/test_api_smoke_qa.py",
    # Adaptive engine wiring: pins ELO range [600, 2400] and the backend→Android
    # strength mapping contract so backend changes break CI immediately.
    "llm/tests/test_adaptive_engine_wiring.py",
    # Server wiring invariants: events/router.py no-print, /explain→SafeExplainer,
    # _record_move_stat safety, log_move call site, unused-model guard.
    "llm/tests/test_server_wiring.py",
    # Dynamic adaptation mode: registry unit tests, Pydantic validation,
    # AST wiring, HTTP stub layer (DA-01 – DA-30 + ELO constant contract).
    "llm/tests/test_dynamic_adaptation.py",
    # SECA integration: SafeExplainer schema alignment, live-pipeline quality
    # passthrough, confidence_language_controller wiring, SkillUpdater action
    # derivation, dynamic-mode ELO convergence, and end-to-end ESV chain.
    "llm/tests/test_seca_integration.py",
    # Player level quality: PLQ-01..10 — skill-level mapping correctness, chat and
    # live-move differentiation across beginner/club/intermediate/advanced, SafeExplainer
    # level-aware output, engine signal integrity across all levels.
    "llm/tests/test_player_level_quality.py",
    # Context compaction: COMPACT-01..14 — threshold trigger, length reduction,
    # blunder/strength/topic preservation, recent-turns verbatim, token savings,
    # integration with generate_chat_reply.
    "llm/tests/test_context_compact.py",
    # LLM response quality: QUAL-01..27 — Mode-1/Mode-2 prompt correctness,
    # SafeExplainer level differentiation, ConfidenceLanguageController tone,
    # deterministic fallback adaptation across beginner/intermediate/advanced.
    "llm/tests/test_llm_quality.py",
    # Prompt injection: INJ-01..20 — input sanitizer pattern coverage, field
    # sanitization for player_profile/past_mistakes/FEN/history, output firewall,
    # Mode-2 negative validator, end-to-end injection resistance.
    "llm/tests/test_prompt_injection.py",
    # Stockfish notation correctness: ESV-01..43 — CP threshold boundaries (20/21/60/61/120/121),
    # eval_delta boundaries (49/50), sign convention, mate path, errors type guard,
    # eval_type normalization, value coercion, FEN enrichment, schema round-trip.
    "llm/tests/test_stockfish_notation.py",
]

COVERAGE_TARGETS = [
    "llm.cache_keys",
    "llm.elite_engine_service",
    "llm.engine_eval",
    "llm.metrics",
    "llm.position_input",
    "llm.predictive_cache",
    "llm.rag.contracts.validate_output",
    "llm.rag.llm.fake",
    "llm.rag.safety.output_firewall",
    "llm.rag.quality.explanation_score",
    "llm.rag.validators.mode_2_negative",
    "llm.rag.validators.mode_2_semantic",
    "llm.rag.validators.mode_2_structure",
    "llm.rag.validators.sanitize",
    "llm.rag.validators.explain_response_schema",
    "llm.rag.prompts.input_sanitizer",
    "llm.rag.engine_signal.extract_engine_signal",
    "llm.seca.analytics.logger",
    "llm.seca.analytics.events",
    "llm.seca.analytics.mistake_stats",
    "llm.seca.analytics.training_recommendations",
    # llm.seca.coach.chat_pipeline is intentionally excluded from --cov targets:
    # llm.seca.coach.__init__ imports engine.py which loads numpy via a C extension;
    # coverage pre-loading the package triggers "cannot load module more than once
    # per process" when test_chat_pipeline.py later re-imports it.
    # chat_pipeline.py logic is fully exercised by test_chat_pipeline.py (26 tests).
    "llm.seca.events.storage",
    "llm.seca.analysis.historical_pipeline",
    "llm.seca.adaptation.coupling",
    "llm.seca.adaptation.dynamic_mode",
    "llm.seca.adaptation.skill_profile",
    "llm.seca.adaptation.teaching_policy",
    "llm.seca.adaptation.opponent_policy",
    "llm.seca.auth.service",
    "llm.seca.auth.hashing",
    "llm.seca.auth.tokens",
    "llm.seca.analytics.router",
    "llm.seca.curriculum.reward",
    "llm.seca.curriculum.spacing",
    "llm.seca.skills.trainer",
    "llm.seca.brain.bandit.contextual_bandit",
    # llm.seca.coach.live_controller and llm.seca.coach.executor are excluded from
    # --cov targets: llm.seca.coach.__init__ imports engine.py which loads numpy via
    # a C extension; coverage pre-loading the package for instrumentation triggers
    # "cannot load module more than once per process" when the tests later re-import.
    # Their logic is fully exercised by TestPostGameCoachRegressionSuite and
    # TestCoachExecutorStability (22 tests).
    "llm.rag.meta.case_classifier",
    "llm.confidence_language_controller",
    "llm.explain_pipeline",
    "llm.seca.explainer.safe_explainer",
    # llm.seca.engines.stockfish.pool is intentionally excluded from coverage targets:
    # the majority of its lines require a live Stockfish process and Redis, which are
    # unavailable in the unit-test environment. The pure logic (FenMoveCache, movetime
    # resolution, fallback) is tested via test_engine_response_format.py.
]


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *TEST_TARGETS,
        *[f"--cov={target}" for target in COVERAGE_TARGETS],
        "--cov-report=term-missing:skip-covered",
        "--cov-report=xml:tmp_logs/coverage.xml",
        "--cov-fail-under=80",
        "--junitxml=tmp_logs/pytest-ci.xml",
    ]

    print("=== RUNNING CI TEST SUITE ===")
    pytest_rc = subprocess.run(cmd, cwd=PROJECT_ROOT, check=False).returncode
    if pytest_rc != 0:
        return pytest_rc

    # Per-module coverage floor enforcement on top of pytest's --cov-fail-under
    # global average gate. Validators and the safety firewall must clear 95%
    # — see llm/check_coverage_thresholds.py for the rationale and full list.
    print("=== RUNNING PER-MODULE COVERAGE GATE ===")
    return subprocess.run(
        [sys.executable, "llm/check_coverage_thresholds.py"],
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
