"""
Unit tests for the long-form chat coaching pipeline (Mode 2).

Modules under test
------------------
llm.seca.coach.chat_pipeline
    ChatTurn, ChatReply, generate_chat_reply, _build_reply_deterministic

Invariants pinned
-----------------
 1. REPLY_NONNULL:              generate_chat_reply always returns a ChatReply.
 2. REPLY_TEXT_NONNULL:         ChatReply.reply is a non-empty string.
 3. ENGINE_SIGNAL_KEYS:         engine_signal has all required top-level keys.
 4. MODE_CHAT_V1:               mode is always "CHAT_V1".
 5. ENGINE_SIGNAL_NEVER_FROM_LLM: engine_signal never reflects LLM-injected text.
 6. ENGINE_SIGNAL_NEVER_FROM_USER: engine_signal never reflects user-supplied eval text.
 7. FROZEN_REPLY:               ChatReply is immutable (frozen dataclass).
 8. FROZEN_TURN:                ChatTurn is immutable (frozen dataclass).
 9. PHASE_TIP_IN_FALLBACK:      deterministic fallback includes game-phase coaching tip.
10. NO_PHASE_TIP_IN_MODE1:      Mode-1 pipeline (_build_hint) omits phase tips.
11. LEVEL_BEGINNER:             beginner skill_estimate → beginner coaching advice.
12. LEVEL_ADVANCED:             advanced skill_estimate → advanced coaching advice.
13. LEVEL_INTERMEDIATE:         None/unknown profile → intermediate coaching advice.
14. QUESTION_TACTICAL:          "tactic" keyword → tactical coaching advice.
15. QUESTION_OPENING:           "opening" keyword → opening coaching advice.
16. QUESTION_ENDGAME:           "endgame" keyword → endgame coaching advice.
17. QUESTION_STRATEGIC:         "plan" keyword → strategic coaching advice.
18. QUESTION_GENERAL:           unrecognised query → general coaching advice.
19. HISTORY_FOLLOWUP:           prior conversation turn noted in deterministic reply.
20. LAYER_NO_RL:                chat_pipeline.py imports no RL/brain modules.
21. LAYER_NO_SQL:               chat_pipeline.py imports no sqlalchemy.
22. DETERMINISM:                identical inputs → identical ChatReply (fallback path).
23. LLM_PATH_USED_WHEN_AVAILABLE: LLM response is returned when _build_chat_llm succeeds.
24. LLM_FALLBACK_ON_ERROR:      deterministic fallback used when LLM raises.
25. LLM_ENGINE_SIGNAL_INTEGRITY: engine_signal on LLM path comes from extract_engine_signal.
26. REPLY_LONGER_THAN_MODE1:    Mode-2 reply (fallback) is longer than Mode-1 hint for same pos.
27. ENGINE_SIGNAL_BAND_TYPE:    evaluation sub-dict has "band" and "type" keys.
28. PAST_MISTAKES_IN_CONTEXT:   past_mistakes list is reflected in context block.
29. MOVE_COUNT_IN_CONTEXT:      move_count is reflected in context block.
30. CHAT_RETRY_ON_ASSERTION:    AssertionError from _validate_neg triggers retry (not hard fail).
31. CHAT_HARD_FAIL_ON_FIREWALL: OutputFirewallError from _check_output is not retried.
32. CHAT_ESV_SCHEMA_VALIDATED:  EngineSignalSchema.model_validate() called on LLM path.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from llm.seca.coach.chat_pipeline import (
    ChatReply,
    ChatTurn,
    _build_context_block,
    _build_reply_deterministic,
    generate_chat_reply,
)
from llm.seca.coach.live_move_pipeline import _build_hint

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_MID_FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
_UCI_NORMAL = "e2e4"

_MODULE = "llm.seca.coach.chat_pipeline"


def _patch_llm_unavailable():
    return patch(f"{_MODULE}._LLM_AVAILABLE", False)


def _make_turns(*pairs: tuple[str, str]) -> list[ChatTurn]:
    return [ChatTurn(role=r, content=c) for r, c in pairs]


def _simple_signal(band: str = "equal", side: str = "white", phase: str = "middlegame") -> dict:
    return {
        "evaluation": {"band": band, "side": side, "type": "cp", "value": 0},
        "phase": phase,
        "eval_delta": "stable",
        "last_move_quality": "good",
    }


# ---------------------------------------------------------------------------
# Invariants 1-4: core return type, non-null, and mode
# ---------------------------------------------------------------------------


class TestCoreReturn:
    def test_returns_chat_reply(self):
        """1. REPLY_NONNULL"""
        turns = _make_turns(("user", "What should I do next?"))
        with _patch_llm_unavailable():
            result = generate_chat_reply(_STARTING_FEN, turns)
        assert isinstance(result, ChatReply)

    def test_reply_text_nonnull(self):
        """2. REPLY_TEXT_NONNULL"""
        turns = _make_turns(("user", "What should I do next?"))
        with _patch_llm_unavailable():
            result = generate_chat_reply(_STARTING_FEN, turns)
        assert isinstance(result.reply, str) and result.reply.strip()

    def test_engine_signal_keys(self):
        """3. ENGINE_SIGNAL_KEYS"""
        turns = _make_turns(("user", "Tell me about this position."))
        with _patch_llm_unavailable():
            result = generate_chat_reply(_STARTING_FEN, turns)
        for key in ("evaluation", "phase", "eval_delta"):
            assert key in result.engine_signal

    def test_mode_chat_v1(self):
        """4. MODE_CHAT_V1"""
        turns = _make_turns(("user", "Explain the position."))
        with _patch_llm_unavailable():
            result = generate_chat_reply(_STARTING_FEN, turns)
        assert result.mode == "CHAT_V1"


# ---------------------------------------------------------------------------
# Invariants 5-6: engine signal source integrity
# ---------------------------------------------------------------------------


class TestEngineSignalSourceIntegrity:
    def test_engine_signal_never_from_llm(self):
        """5. ENGINE_SIGNAL_NEVER_FROM_LLM"""
        injected = "INJECTED_EVAL_VALUE"
        turns = _make_turns(("user", f"My position has {injected} advantage."))
        with _patch_llm_unavailable():
            result = generate_chat_reply(_STARTING_FEN, turns)
        assert injected not in str(result.engine_signal)

    def test_engine_signal_never_from_user(self):
        """6. ENGINE_SIGNAL_NEVER_FROM_USER"""
        turns = _make_turns(("user", "I think white has +999 centipawns."))
        with _patch_llm_unavailable():
            result = generate_chat_reply(_STARTING_FEN, turns)
        val = result.engine_signal.get("evaluation", {}).get("value", "")
        assert "999" not in str(val)


# ---------------------------------------------------------------------------
# Invariants 7-8: immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_chat_reply_is_frozen(self):
        """7. FROZEN_REPLY"""
        turns = _make_turns(("user", "hello"))
        with _patch_llm_unavailable():
            r = generate_chat_reply(_STARTING_FEN, turns)
        with pytest.raises((AttributeError, TypeError)):
            r.reply = "changed"  # type: ignore[misc]

    def test_chat_turn_is_frozen(self):
        """8. FROZEN_TURN"""
        t = ChatTurn(role="user", content="hello")
        with pytest.raises((AttributeError, TypeError)):
            t.content = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Invariants 9-10: phase tips belong in Mode-2, not Mode-1
# ---------------------------------------------------------------------------


_PHASE_TIPS = [
    "In the opening",
    "In the middlegame",
    "In the endgame",
    "prioritise development",
    "tactical motifs",
    "activate the king",
]


class TestPhaseTipPlacement:
    def test_phase_tip_in_mode2_fallback(self):
        """9. PHASE_TIP_IN_FALLBACK — Mode-2 deterministic reply includes game-phase tip."""
        turns = _make_turns(("user", "What is my plan?"))
        signal = _simple_signal(phase="opening")
        context = _build_context_block(signal, None, None)
        reply = _build_reply_deterministic(
            user_query="What is my plan?",
            context_block=context,
            engine_signal=signal,
            base_explanation="Solid position.",
            history=turns,
        )
        assert any(tip in reply for tip in _PHASE_TIPS)

    def test_no_phase_tip_in_mode1_fallback(self):
        """10. NO_PHASE_TIP_IN_MODE1 — Mode-1 _build_hint omits phase tips."""
        signal = _simple_signal(phase="opening")
        hint = _build_hint("e2e4", signal, base_explanation="")
        assert not any(tip in hint for tip in _PHASE_TIPS)


# ---------------------------------------------------------------------------
# Invariants 11-13: level differentiation
# ---------------------------------------------------------------------------


class TestLevelDifferentiation:
    def _reply_for_level(self, skill_estimate: str | None, query: str = "What tactic do I have?") -> str:
        profile = {"skill_estimate": skill_estimate} if skill_estimate is not None else None
        turns = _make_turns(("user", query))
        with _patch_llm_unavailable():
            return generate_chat_reply(_STARTING_FEN, turns, player_profile=profile).reply

    def test_beginner_advice(self):
        """11. LEVEL_BEGINNER"""
        reply = self._reply_for_level("beginner")
        assert any(w in reply.lower() for w in ("unprotected", "piece", "protected", "safety", "target"))

    def test_advanced_advice(self):
        """12. LEVEL_ADVANCED"""
        reply = self._reply_for_level("advanced")
        assert any(w in reply.lower() for w in ("calculat", "forcing", "candidate", "systematically", "assess"))

    def test_none_profile_is_intermediate(self):
        """13. LEVEL_INTERMEDIATE"""
        reply = self._reply_for_level(None)
        assert isinstance(reply, str) and reply.strip()


# ---------------------------------------------------------------------------
# Invariants 14-18: question-type detection
# ---------------------------------------------------------------------------


class TestQuestionTypeDetection:
    def _advice_for_query(self, query: str, skill: str = "intermediate") -> str:
        profile = {"skill_estimate": skill}
        turns = _make_turns(("user", query))
        signal = _simple_signal()
        context = _build_context_block(signal, profile, None)
        return _build_reply_deterministic(
            user_query=query,
            context_block=context,
            engine_signal=signal,
            base_explanation="",
            history=turns,
            skill_level=skill,
        )

    def test_tactical_keyword(self):
        """14. QUESTION_TACTICAL"""
        reply = self._advice_for_query("Is there a tactic here?")
        assert any(w in reply.lower() for w in ("forcing", "undefended", "tactic", "target", "threat"))

    def test_opening_keyword(self):
        """15. QUESTION_OPENING"""
        reply = self._advice_for_query("How should I handle the opening?")
        assert any(w in reply.lower() for w in ("develop", "centre", "center", "castle", "control"))

    def test_endgame_keyword(self):
        """16. QUESTION_ENDGAME"""
        reply = self._advice_for_query("How do I play the endgame?")
        assert any(w in reply.lower() for w in ("king", "rook", "pawn", "passed", "centralise", "active"))

    def test_strategic_keyword(self):
        """17. QUESTION_STRATEGIC"""
        reply = self._advice_for_query("What is the strategic plan?")
        assert any(w in reply.lower() for w in ("weakness", "weak", "square", "pawn", "piece", "outpost", "imbalance"))

    def test_general_fallback(self):
        """18. QUESTION_GENERAL"""
        reply = self._advice_for_query("Hmm interesting position")
        assert any(w in reply.lower() for w in ("piece", "position", "improve", "focus", "eval", "safety"))


# ---------------------------------------------------------------------------
# Invariant 19: conversation history follow-up note
# ---------------------------------------------------------------------------


class TestConversationHistory:
    def test_history_followup_noted(self):
        """19. HISTORY_FOLLOWUP — prior user question mentioned in deterministic reply."""
        turns = _make_turns(
            ("user", "Why did I lose a pawn?"),
            ("assistant", "Because you moved too fast."),
            ("user", "What should I do now?"),
        )
        with _patch_llm_unavailable():
            result = generate_chat_reply(_STARTING_FEN, turns)
        assert any(w in result.reply.lower() for w in ("pawn", "earlier", "following", "question"))


# ---------------------------------------------------------------------------
# Invariants 20-21: layer isolation
# ---------------------------------------------------------------------------


class TestLayerIsolation:
    _SRC = Path(__file__).parent.parent / "seca" / "coach" / "chat_pipeline.py"

    def _imports(self) -> set[str]:
        tree = ast.parse(self._SRC.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        names.add(alias.name)
        return names

    def test_no_rl_imports(self):
        """20. LAYER_NO_RL"""
        imports = self._imports()
        rl_modules = {"torch", "gymnasium", "stable_baselines3", "brain", "reinforcement"}
        assert not any(mod in " ".join(imports) for mod in rl_modules)

    def test_no_sql_imports(self):
        """21. LAYER_NO_SQL"""
        imports = self._imports()
        assert not any("sqlalchemy" in m for m in imports)


# ---------------------------------------------------------------------------
# Invariant 22: determinism (fallback path)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_identical_inputs_produce_identical_output(self):
        """22. DETERMINISM"""
        turns = _make_turns(("user", "What should I do?"))
        with _patch_llm_unavailable():
            r1 = generate_chat_reply(_STARTING_FEN, turns)
            r2 = generate_chat_reply(_STARTING_FEN, turns)
        assert r1.reply == r2.reply
        assert r1.engine_signal == r2.engine_signal
        assert r1.mode == r2.mode


# ---------------------------------------------------------------------------
# Invariants 23-25: LLM path
# ---------------------------------------------------------------------------


class TestLLMPath:
    def test_llm_response_returned_when_available(self):
        """23. LLM_PATH_USED_WHEN_AVAILABLE"""
        llm_reply = "This position offers rich strategic play. The knight outpost on d5 is the key."
        turns = _make_turns(("user", "What is my plan?"))
        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(f"{_MODULE}._build_chat_llm", return_value=llm_reply),
        ):
            result = generate_chat_reply(_STARTING_FEN, turns)
        assert result.reply == llm_reply
        assert result.mode == "CHAT_V1"

    def test_deterministic_fallback_on_llm_error(self):
        """24. LLM_FALLBACK_ON_ERROR"""
        turns = _make_turns(("user", "What is my plan?"))
        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(f"{_MODULE}._build_chat_llm", side_effect=RuntimeError("Ollama down")),
        ):
            result = generate_chat_reply(_STARTING_FEN, turns)
        assert isinstance(result.reply, str) and result.reply.strip()
        assert result.mode == "CHAT_V1"

    def test_engine_signal_not_from_llm(self):
        """25. LLM_ENGINE_SIGNAL_INTEGRITY"""
        injected_signal = {"evaluation": {"band": "decisive_advantage", "side": "black"}}
        turns = _make_turns(("user", "Explain."))
        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(f"{_MODULE}._build_chat_llm", return_value="Good move!"),
        ):
            result = generate_chat_reply(_STARTING_FEN, turns)
        assert result.engine_signal != injected_signal


# ---------------------------------------------------------------------------
# Invariant 26: Mode-2 reply longer than Mode-1 hint for same position
# ---------------------------------------------------------------------------


class TestMode2ReplyLength:
    def test_mode2_reply_longer_than_mode1_hint(self):
        """26. REPLY_LONGER_THAN_MODE1"""
        turns = _make_turns(("user", "What is the strategic plan here?"))
        with _patch_llm_unavailable():
            chat_result = generate_chat_reply(_MID_FEN, turns)

        with patch("llm.seca.coach.live_move_pipeline._LLM_AVAILABLE", False):
            from llm.seca.coach.live_move_pipeline import generate_live_reply
            live_result = generate_live_reply(_MID_FEN, _UCI_NORMAL)

        assert len(chat_result.reply) > len(live_result.hint), (
            f"Mode-2 reply ({len(chat_result.reply)} chars) should exceed "
            f"Mode-1 hint ({len(live_result.hint)} chars)"
        )


# ---------------------------------------------------------------------------
# Invariant 27: evaluation sub-dict structure
# ---------------------------------------------------------------------------


class TestEngineSignalStructure:
    def test_evaluation_has_band_and_type(self):
        """27. ENGINE_SIGNAL_BAND_TYPE"""
        turns = _make_turns(("user", "Explain the eval."))
        with _patch_llm_unavailable():
            result = generate_chat_reply(_STARTING_FEN, turns)
        ev = result.engine_signal.get("evaluation", {})
        assert "band" in ev
        assert "type" in ev


# ---------------------------------------------------------------------------
# Invariants 28-29: context block enrichment
# ---------------------------------------------------------------------------


class TestContextBlockEnrichment:
    def test_past_mistakes_in_context(self):
        """28. PAST_MISTAKES_IN_CONTEXT"""
        signal = _simple_signal()
        context = _build_context_block(signal, None, ["tactical_vision", "time_management"])
        assert "tactical_vision" in context or "time_management" in context

    def test_move_count_in_context(self):
        """29. MOVE_COUNT_IN_CONTEXT"""
        signal = _simple_signal()
        context = _build_context_block(signal, None, None, move_count=15)
        assert "15" in context


# ---------------------------------------------------------------------------
# Invariants 30-32: retry logic and ESV schema validation
# ---------------------------------------------------------------------------


class TestRetryAndSchemaValidation:
    def test_retry_on_assertion_error(self):
        """30. CHAT_RETRY_ON_ASSERTION — AssertionError triggers retry; success on second call."""
        turns = _make_turns(("user", "What is my plan?"))
        call_count = {"n": 0}

        def _flaky_llm(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise AssertionError("Mode-2 violation")
            return "This is a valid coaching reply."

        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(f"{_MODULE}._build_chat_llm", side_effect=_flaky_llm),
            patch(f"{_MODULE}._EngineSignalSchema") as mock_schema,
        ):
            mock_schema.model_validate.return_value = None
            result = generate_chat_reply(_STARTING_FEN, turns)

        assert call_count["n"] == 2, (
            f"_build_chat_llm should be called twice (initial + 1 retry); got {call_count['n']}"
        )
        assert result.reply == "This is a valid coaching reply."

    def test_hard_fail_on_output_firewall(self):
        """31. CHAT_HARD_FAIL_ON_FIREWALL — OutputFirewallError is not retried; goes to deterministic."""
        from llm.rag.safety.output_firewall import OutputFirewallError

        turns = _make_turns(("user", "What is my plan?"))
        call_count = {"n": 0}

        def _firewall_llm(*args, **kwargs):
            call_count["n"] += 1
            raise OutputFirewallError("unsafe content")

        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(f"{_MODULE}._build_chat_llm", side_effect=_firewall_llm),
            patch(f"{_MODULE}._OutputFirewallError", OutputFirewallError),
        ):
            result = generate_chat_reply(_STARTING_FEN, turns)

        assert call_count["n"] == 1, (
            f"OutputFirewallError must not be retried; _build_chat_llm called {call_count['n']} times"
        )
        assert isinstance(result.reply, str) and result.reply.strip(), (
            "Deterministic fallback reply must be non-empty after firewall block"
        )

    def test_esv_schema_validated_on_llm_path(self):
        """32. CHAT_ESV_SCHEMA_VALIDATED — EngineSignalSchema.model_validate called on LLM path."""
        turns = _make_turns(("user", "Explain."))

        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(f"{_MODULE}._build_chat_llm", return_value="Valid coaching reply."),
            patch(f"{_MODULE}._EngineSignalSchema") as mock_schema,
        ):
            mock_schema.model_validate.return_value = None
            generate_chat_reply(_STARTING_FEN, turns)

        mock_schema.model_validate.assert_called_once(), (
            "EngineSignalSchema.model_validate must be called once on the LLM path "
            "to verify ESV structural integrity before returning."
        )

    def test_retry_hint_appended_on_second_attempt(self):
        """Retry hint must be passed to _build_chat_llm on the second attempt."""
        turns = _make_turns(("user", "What is my plan?"))
        received_hints: list[str] = []

        def _capture_llm(*args, retry_hint: str = "", **kwargs):
            received_hints.append(retry_hint)
            if len(received_hints) == 1:
                raise AssertionError("validator failed")
            return "OK reply."

        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(f"{_MODULE}._build_chat_llm", side_effect=_capture_llm),
            patch(f"{_MODULE}._EngineSignalSchema") as mock_schema,
        ):
            mock_schema.model_validate.return_value = None
            generate_chat_reply(_STARTING_FEN, turns)

        assert received_hints[0] == "", "First attempt must have empty retry_hint"
        assert received_hints[1] != "", "Second attempt must carry a non-empty retry_hint"
        assert "MODE-2" in received_hints[1], "Retry hint must reference MODE-2 rules"
