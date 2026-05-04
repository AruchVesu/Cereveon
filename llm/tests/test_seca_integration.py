"""
SECA integration tests — verify all layers wire together correctly.

Covers:
  SINT-01..04 : SafeExplainer schema alignment with extract_engine_signal()
  SINT-05..07 : Live move pipeline quality-awareness (stockfish_json passthrough)
  SINT-08..10 : confidence_language_controller wired into live coaching
  SINT-11..12 : Chat pipeline engine-signal contract
  SINT-13..14 : Dynamic mode ELO shifts with known quality labels
  SINT-15..16 : SkillUpdater action derived from weaknesses, not hardcoded
  SINT-17..18 : Historical pipeline → training recommendations chain
  SINT-19..20 : End-to-end ESV → SafeExplainer → hint quality chain
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# SINT-01..04  SafeExplainer schema alignment
# ---------------------------------------------------------------------------


class TestSafeExplainerSchemaAlignment:
    """SafeExplainer.explain() must produce non-empty output for any valid ESV signal.

    extract_engine_signal() returns evaluation.{type,band,side} — NOT flat
    eval_cp / best_move / mate_in keys.  SafeExplainer must read the ESV schema.
    """

    def test_sint01_cp_clear_advantage_produces_output(self):
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
        from llm.seca.explainer.safe_explainer import SafeExplainer

        signal = extract_engine_signal({"evaluation": {"type": "cp", "value": 150}})
        result = SafeExplainer().explain(signal)
        assert result != "", (
            "SafeExplainer returned empty for a clear-advantage signal. "
            "It must read evaluation.band, not the legacy eval_cp key."
        )
        assert len(result) > 10

    def test_sint02_mate_signal_produces_output(self):
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
        from llm.seca.explainer.safe_explainer import SafeExplainer

        signal = extract_engine_signal(
            {"evaluation": {"type": "mate", "value": 2}},
            fen="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1",
        )
        result = SafeExplainer().explain(signal)
        assert result != "", "SafeExplainer returned empty for a mate signal."
        assert "mate" in result.lower() or "winning" in result.lower()

    @pytest.mark.parametrize(
        "value,expected_fragment",
        [
            (0,   "equal"),
            (45,  "advantage"),
            (100, "advantage"),
            (300, "advantage"),
            (-200, "advantage"),
        ],
    )
    def test_sint03_all_bands_produce_nonempty_output(self, value, expected_fragment):
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
        from llm.seca.explainer.safe_explainer import SafeExplainer

        signal = extract_engine_signal({"evaluation": {"type": "cp", "value": value}})
        result = SafeExplainer().explain(signal)
        assert result != "", f"SafeExplainer returned empty for value={value}"
        assert expected_fragment in result.lower(), (
            f"Expected '{expected_fragment}' in SafeExplainer output for value={value}: {result!r}"
        )

    def test_sint04_safe_explainer_reads_esv_keys_not_legacy_keys(self):
        """SafeExplainer must NOT read legacy flat keys (eval_cp, mate_in, etc.)."""
        src = (PROJECT_ROOT / "llm/seca/explainer/safe_explainer.py").read_text()
        tree = ast.parse(src)

        legacy_keys = {"eval_cp", "mate_in", "blunder_type", "material_balance"}
        found_legacy = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in legacy_keys:
                    found_legacy.add(node.value)

        assert not found_legacy, (
            f"SafeExplainer reads legacy keys {found_legacy} that extract_engine_signal() "
            "never produces.  Update SafeExplainer to read evaluation.band/type/side."
        )


# ---------------------------------------------------------------------------
# SINT-05..07  Live move pipeline quality-awareness
# ---------------------------------------------------------------------------


class TestLiveMovePipelineQuality:
    """generate_live_reply() must expose stockfish_json quality data through its hint."""

    SAMPLE_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"

    def test_sint05_live_reply_contains_engine_reference(self):
        from llm.seca.coach.live_move_pipeline import generate_live_reply

        reply = generate_live_reply(fen=self.SAMPLE_FEN, uci="e2e4")
        assert "engine" in reply.hint.lower(), (
            f"Live hint must reference engine evaluation: {reply.hint!r}"
        )

    def test_sint06_blunder_quality_in_hint_when_stockfish_json_provided(self):
        """When stockfish_json carries last_move_quality=blunder the hint must say so."""
        from llm.seca.coach.live_move_pipeline import generate_live_reply

        reply = generate_live_reply(
            fen=self.SAMPLE_FEN,
            uci="e2e4",
            stockfish_json={"errors": {"last_move_quality": "blunder"}},
        )
        assert reply.move_quality == "blunder", (
            f"move_quality must be 'blunder' when provided via stockfish_json; "
            f"got {reply.move_quality!r}"
        )
        assert "blunder" in reply.hint.lower(), (
            f"Hint must contain blunder feedback when quality is 'blunder': {reply.hint!r}"
        )

    def test_sint07_excellent_quality_in_hint_when_stockfish_json_provided(self):
        from llm.seca.coach.live_move_pipeline import generate_live_reply

        reply = generate_live_reply(
            fen=self.SAMPLE_FEN,
            uci="e2e4",
            stockfish_json={"errors": {"last_move_quality": "excellent"}},
        )
        assert reply.move_quality == "excellent"
        assert "excellent" in reply.hint.lower(), (
            f"Hint must contain excellence feedback: {reply.hint!r}"
        )


# ---------------------------------------------------------------------------
# SINT-08..10  confidence_language_controller wired into coaching
# ---------------------------------------------------------------------------


class TestConfidenceLanguageControllerWiring:
    """confidence_language_controller must be imported and used in live_move_pipeline."""

    def test_sint08_language_controller_imported_in_live_pipeline(self):
        src = (PROJECT_ROOT / "llm/seca/coach/live_move_pipeline.py").read_text()
        tree = ast.parse(src)
        imported_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_names.append(node.module)
                for alias in node.names:
                    imported_names.append(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.append(alias.name)

        joined = " ".join(imported_names)
        assert "confidence_language_controller" in joined or "build_language_controller_block" in joined, (
            "live_move_pipeline.py must import confidence_language_controller to produce "
            "human-like coaching responses adapted to position urgency."
        )

    def test_sint09_urgency_is_critical_on_blunder_signal(self):
        from llm.confidence_language_controller import compute_urgency
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

        signal = extract_engine_signal({"errors": {"last_move_quality": "blunder"}})
        assert compute_urgency(signal) == "critical", (
            "Blunder must produce 'critical' urgency in the language controller."
        )

    def test_sint10_urgency_is_calm_on_neutral_signal(self):
        from llm.confidence_language_controller import compute_urgency
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

        signal = extract_engine_signal({})
        assert compute_urgency(signal) == "calm", (
            "Neutral (empty) signal must produce 'calm' urgency."
        )

    def test_sint10b_hint_is_more_urgent_on_blunder_than_good_move(self):
        """Blunder hint must be lexically distinguishable from a good-move hint."""
        from llm.seca.coach.live_move_pipeline import generate_live_reply

        FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"

        blunder_reply = generate_live_reply(
            fen=FEN,
            uci="e2e4",
            stockfish_json={"errors": {"last_move_quality": "blunder"}},
        )
        good_reply = generate_live_reply(
            fen=FEN,
            uci="e2e4",
            stockfish_json={"errors": {"last_move_quality": "good"}},
        )

        assert blunder_reply.hint != good_reply.hint, (
            "Blunder and good-move hints must be different — quality must affect the coaching text."
        )


# ---------------------------------------------------------------------------
# SINT-11..12  Chat pipeline engine-signal contract
# ---------------------------------------------------------------------------


class TestChatPipelineEngineSignalContract:
    SAMPLE_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"

    def test_sint11_chat_reply_references_engine_evaluation(self):
        from llm.seca.coach.chat_pipeline import ChatTurn, generate_chat_reply

        reply = generate_chat_reply(
            fen=self.SAMPLE_FEN,
            messages=[ChatTurn(role="user", content="What should I do here?")],
        )
        assert reply.mode == "CHAT_V1"
        assert "engine" in reply.reply.lower() or "evaluation" in reply.reply.lower(), (
            f"Chat reply must reference the engine evaluation: {reply.reply!r}"
        )

    def test_sint12_chat_engine_signal_has_evaluation_key(self):
        from llm.seca.coach.chat_pipeline import ChatTurn, generate_chat_reply

        reply = generate_chat_reply(
            fen=self.SAMPLE_FEN,
            messages=[ChatTurn(role="user", content="Analyze my position.")],
        )
        assert "evaluation" in reply.engine_signal
        assert reply.engine_signal["evaluation"]["type"] in ("cp", "mate")
        assert "band" in reply.engine_signal["evaluation"]
        assert "side" in reply.engine_signal["evaluation"]


# ---------------------------------------------------------------------------
# SINT-13..14  Dynamic mode ELO convergence
# ---------------------------------------------------------------------------


class TestDynamicModeEloConvergence:
    """DynamicModeRegistry ELO must shift when actual quality labels are recorded."""

    def test_sint13_elo_decreases_after_blunder(self):
        from llm.seca.adaptation.dynamic_mode import DynamicModeRegistry

        registry = DynamicModeRegistry()
        registry.set_mode("p1", enabled=True, base_elo=1200)
        registry.record_move_quality("p1", "blunder")
        state = registry.get_state("p1")
        assert state.current_elo < 1200, (
            f"ELO must decrease after blunder; got {state.current_elo}"
        )

    def test_sint14_elo_does_not_shift_for_unknown_quality(self):
        from llm.seca.adaptation.dynamic_mode import DynamicModeRegistry

        registry = DynamicModeRegistry()
        registry.set_mode("p2", enabled=True, base_elo=1200)
        registry.record_move_quality("p2", "unknown")
        state = registry.get_state("p2")
        assert state.current_elo == 1200, (
            f"ELO must not shift for unknown quality; got {state.current_elo}"
        )

    def test_sint14b_elo_convergence_series(self):
        """Several good moves followed by blunders should converge toward a lower ELO."""
        from llm.seca.adaptation.dynamic_mode import DynamicModeRegistry

        registry = DynamicModeRegistry()
        registry.set_mode("p3", enabled=True, base_elo=1500)

        for _ in range(3):
            registry.record_move_quality("p3", "blunder")
        for _ in range(3):
            registry.record_move_quality("p3", "good")

        state = registry.get_state("p3")
        # 3 blunders (-40 each = -120) + 3 good (+10 each = +30) → net -90 → 1410
        assert state.current_elo < 1500, (
            f"ELO should be below 1500 after net-negative series; got {state.current_elo}"
        )
        assert state.move_count == 6


# ---------------------------------------------------------------------------
# SINT-15..16  SkillUpdater action not hardcoded
# ---------------------------------------------------------------------------


class TestSkillUpdaterAction:
    """SkillUpdater must derive the bandit action from actual weaknesses."""

    def test_sint15_action_not_hardcoded_string_literal(self):
        src = (PROJECT_ROOT / "llm/seca/skills/updater.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not (isinstance(target, ast.Name) and target.id == "action"):
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    pytest.fail(
                        f"SkillUpdater has action = {node.value.value!r} (hardcoded). "
                        "Derive the action from dominant weakness instead."
                    )

    def test_sint16_update_from_event_uses_dominant_weakness_as_action(self):
        """When weaknesses={'opening': 0.9, 'tactics': 0.1}, action must be 'opening'."""
        import json
        from unittest.mock import MagicMock, patch, call

        from llm.seca.skills.updater import SkillUpdater

        player = MagicMock()
        player.id = "p1"
        player.rating = 1000.0
        player.confidence = 0.5
        player.skill_vector_json = json.dumps({})
        player.player_embedding = None

        event = MagicMock()
        event.result = "win"
        event.accuracy = 0.75
        event.weaknesses_json = json.dumps({"opening": 0.9, "tactics": 0.1})

        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = player

        logged_actions: list[str] = []

        real_log = None

        class TrackingStore:
            def __init__(self, db):
                pass

            def log(self, player_id, context, action, reward):
                logged_actions.append(action)

        with patch("llm.seca.skills.updater.ExperienceStore", TrackingStore), \
             patch("llm.seca.skills.updater.PlayerEmbeddingEncoder") as enc_cls, \
             patch("llm.seca.skills.updater.embedding_from_json", return_value=None), \
             patch("llm.seca.skills.updater.embedding_to_json", return_value="{}"), \
             patch("llm.seca.skills.updater.build_context_vector", return_value=[0.0]):
            enc_cls.return_value.encode.return_value = [0.0]
            updater = SkillUpdater(db)
            updater.update_from_event("p1", event)

        assert logged_actions, "ExperienceStore.log must be called"
        assert logged_actions[0] == "opening", (
            f"Action should be 'opening' (dominant weakness), got {logged_actions[0]!r}. "
            "SkillUpdater must derive action from weaknesses, not hardcode 'tactics'."
        )


# ---------------------------------------------------------------------------
# SINT-17..18  Historical pipeline → training recommendations
# ---------------------------------------------------------------------------


class TestHistoricalPipelineRecommendations:
    def test_sint17_recommendations_produced_from_weakness_data(self):
        from llm.seca.analytics.mistake_stats import aggregate_from_weakness_dicts
        from llm.seca.analytics.training_recommendations import generate_training_recommendations

        weakness_dicts = [{"opening": 0.8}] * 5
        stats = aggregate_from_weakness_dicts(weakness_dicts)
        recs = generate_training_recommendations(stats)
        assert len(recs) > 0, (
            "generate_training_recommendations must return at least one recommendation "
            "for significant opening weakness."
        )

    def test_sint18_high_priority_for_dominant_category(self):
        from llm.seca.analytics.mistake_stats import aggregate_from_weakness_dicts
        from llm.seca.analytics.training_recommendations import generate_training_recommendations

        # Very high tactical weakness → should be high priority
        weakness_dicts = [{"middlegame": 1.0}] * 10
        stats = aggregate_from_weakness_dicts(weakness_dicts)
        recs = generate_training_recommendations(stats)
        priorities = {r.priority for r in recs}
        assert "high" in priorities or "medium" in priorities, (
            f"A dominant weakness must produce high/medium priority recommendation; got {priorities}"
        )


# ---------------------------------------------------------------------------
# SINT-19..20  End-to-end ESV → SafeExplainer → hint quality chain
# ---------------------------------------------------------------------------


class TestEndToEndESVChain:
    """The complete signal chain: stockfish_json → ESV → SafeExplainer → hint."""

    def test_sint19_esv_to_safe_explainer_to_hint_chain(self):
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
        from llm.seca.explainer.safe_explainer import SafeExplainer

        stockfish_json = {
            "evaluation": {"type": "cp", "value": 250},
            "errors": {"last_move_quality": "blunder"},
            "phase": "middlegame",
        }
        signal = extract_engine_signal(stockfish_json)
        explanation = SafeExplainer().explain(signal)

        assert signal["evaluation"]["band"] == "decisive_advantage"
        assert signal["last_move_quality"] == "blunder"
        assert explanation != "", "SafeExplainer must explain the decisive advantage."
        assert "blunder" in explanation.lower() or "advantage" in explanation.lower(), (
            f"Explanation must reference the blunder or the advantage: {explanation!r}"
        )

    def test_sint20_esv_schema_contract(self):
        """extract_engine_signal() output must always contain the required ESV keys."""
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

        signal = extract_engine_signal({"evaluation": {"type": "cp", "value": 100}})

        required_keys = {"evaluation", "eval_delta", "last_move_quality", "tactical_flags", "phase"}
        missing = required_keys - signal.keys()
        assert not missing, f"ESV signal missing required keys: {missing}"

        eval_info = signal["evaluation"]
        assert "type" in eval_info
        assert "band" in eval_info
        assert "side" in eval_info
        assert eval_info["type"] in ("cp", "mate")
        assert eval_info["band"] in (
            "equal", "small_advantage", "clear_advantage", "decisive_advantage"
        )
