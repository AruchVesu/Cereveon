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
33. CHAT_EXHAUSTION_WARNING:    retry exhaustion via repeated AssertionError /
                                Mode2Violation emits a WARNING that mirrors
                                live_move_pipeline's exhaustion log format,
                                closing the observability gap that hid the
                                Mode-2 fallback path from operators (2026-05-15).
34. NO_METADATA_LEAK:           the deterministic Mode-2 reply does NOT
                                contain raw LLM-prompt context markers
                                ("Player skill level:", "This is move N of
                                the game.", "Recurring mistake areas:",
                                "Strengths:", "Recent training focus:").
                                Pre-PR-#169 these leaked verbatim into
                                coach replies (caught on-device 2026-05-16).
35. NO_DUPLICATE_EVAL_SENTENCE: the deterministic reply does not contain
                                two near-identical eval sentences — pre-
                                PR-#169 the SafeExplainer output ("Position
                                is roughly equal.") was appended on top of
                                _format_engine_context's eval sentence
                                ("The position is roughly equal in the
                                opening.") producing the redundant
                                phrasing the user reported.
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
        reply = _build_reply_deterministic(
            user_query="What is my plan?",
            engine_signal=signal,
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
        turns = _make_turns(("user", query))
        signal = _simple_signal()
        return _build_reply_deterministic(
            user_query=query,
            engine_signal=signal,
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
    """Unit tests for ``_build_context_block``.  This helper stays
    after PR #169 as the F-09 sanitisation surface for
    ``test_prompt_injection.py`` — its output is consumed by the LLM
    prompt path only, NOT by the deterministic user-visible reply (PR
    #169 removed that call site).  TestDeterministicReplyNoMetadataLeak
    below is the inverse pin that the user-visible reply does not
    contain this metadata."""

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


# ---------------------------------------------------------------------------
# Invariant 33: retry-exhaustion observability
#
# Context: the chat retry loop used to fall through to the deterministic
# fallback SILENTLY when AssertionError / Mode2Violation fired
# _CHAT_MAX_RETRIES+1 times in a row — there was no WARNING log to tell
# operators that the LLM path bailed.  live_move_pipeline.py logged its
# exhaustion path; chat_pipeline.py didn't.  That asymmetry hid the
# Mode-2 fallback from production observability — operators saw the
# templated deterministic reply with no signal that the LLM path had
# failed, leaving users perceiving Mode-2 as "always templated" with no
# operator-visible cause (caught 2026-05-15 on-device).
#
# PR #168 added a for/else clause to the retry loop that emits a WARNING
# matching live_move_pipeline's format ("Mode-{1,2} LLM failed after N
# attempts (Type: msg); using deterministic fallback") on natural
# exhaustion via validator rejection.  The break-paths (firewall block,
# transport error) keep their existing log levels.
# ---------------------------------------------------------------------------


class TestExhaustionWarning:
    """When the chat retry loop exhausts via repeated validator
    rejection, a WARNING must surface so operators can correlate
    'templated chat reply' user complaints with their root cause."""

    def test_CHAT_EXHAUSTION_WARNING_assertion_error_path(self, caplog):
        """AssertionError on every attempt → WARNING fired, format
        matches live_move_pipeline's exhaustion log."""
        import logging

        turns = _make_turns(("user", "What is my plan?"))

        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(
                f"{_MODULE}._build_chat_llm",
                side_effect=AssertionError("Forbidden MODE-2 pattern detected: pattern `\\bforce(?:d)? mate\\b`"),
            ),
            patch(f"{_MODULE}._EngineSignalSchema") as mock_schema,
            patch(f"{_MODULE}._CHAT_RETRY_DELAY_SECONDS", 0),
            caplog.at_level(logging.WARNING, logger=_MODULE),
        ):
            mock_schema.model_validate.return_value = None
            result = generate_chat_reply(_STARTING_FEN, turns)

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings, (
            "Expected at least one WARNING after AssertionError exhausted the "
            "chat retry loop, got none.  Without this log, operators have no "
            "signal that Mode-2 silently fell through to the deterministic "
            "fallback — the 2026-05-15 observability gap is back."
        )
        msg = warnings[-1].getMessage()
        assert "Mode-2 LLM failed after" in msg, (
            f"WARNING message must contain 'Mode-2 LLM failed after' for "
            f"log-grep parity with live_move_pipeline; got: {msg!r}"
        )
        assert "AssertionError" in msg, (
            f"WARNING message must surface the exception type so operators "
            f"can correlate with the lexical/semantic validator that rejected "
            f"the LLM output; got: {msg!r}"
        )
        assert "using deterministic fallback" in msg, (
            f"WARNING message must explicitly say 'using deterministic "
            f"fallback' for cross-pipeline log-grep parity; got: {msg!r}"
        )
        # Sanity: the deterministic fallback DID fire and produced a reply.
        assert isinstance(result.reply, str) and result.reply.strip()
        assert result.mode == "CHAT_V1"

    def test_CHAT_EXHAUSTION_WARNING_mode2violation_path(self, caplog):
        """Mode2Violation on every attempt → same WARNING fires."""
        import logging

        from llm.rag.validators.mode_2_semantic import Mode2Violation

        turns = _make_turns(("user", "What is my plan?"))

        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(
                f"{_MODULE}._build_chat_llm",
                side_effect=Mode2Violation("Mate not described as forced/inevitable"),
            ),
            patch(f"{_MODULE}._EngineSignalSchema") as mock_schema,
            patch(f"{_MODULE}._CHAT_RETRY_DELAY_SECONDS", 0),
            caplog.at_level(logging.WARNING, logger=_MODULE),
        ):
            mock_schema.model_validate.return_value = None
            generate_chat_reply(_STARTING_FEN, turns)

        warnings = [
            r for r in caplog.records
            if r.levelname == "WARNING" and "Mode-2 LLM failed after" in r.getMessage()
        ]
        assert warnings, (
            "Mode2Violation exhaustion must also fire the WARNING — both "
            "validator-rejection exception types (AssertionError, "
            "Mode2Violation) drive the same fallback path."
        )
        msg = warnings[-1].getMessage()
        assert "Mode2Violation" in msg, (
            f"WARNING must surface the Mode2Violation type so operators can "
            f"distinguish semantic rejections from lexical ones; got: {msg!r}"
        )

    def test_CHAT_EXHAUSTION_WARNING_not_fired_on_success(self, caplog):
        """If the LLM path succeeds on any attempt, the exhaustion
        WARNING must NOT fire — only the natural-exhaustion path emits
        it.  Locks against future regressions where the WARNING
        accidentally fires on every chat call."""
        import logging

        turns = _make_turns(("user", "What is my plan?"))

        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(f"{_MODULE}._build_chat_llm", return_value="A valid LLM reply."),
            patch(f"{_MODULE}._EngineSignalSchema") as mock_schema,
            caplog.at_level(logging.WARNING, logger=_MODULE),
        ):
            mock_schema.model_validate.return_value = None
            generate_chat_reply(_STARTING_FEN, turns)

        spurious = [
            r for r in caplog.records
            if "Mode-2 LLM failed after" in r.getMessage()
        ]
        assert not spurious, (
            f"Exhaustion WARNING must not fire when the LLM path succeeds; "
            f"got: {[r.getMessage() for r in spurious]}"
        )


# ---------------------------------------------------------------------------
# Invariants 34-35: deterministic-reply UX cleanup (PR #169)
#
# On-device test 2026-05-16 surfaced two related UX bugs in the
# deterministic chat fallback:
#
#   1. Raw LLM-prompt context leaked into the user-visible reply —
#      "This is move 6 of the game. Player skill level: intermediate."
#      appeared verbatim in coach text.
#   2. The eval sentence was duplicated — _format_engine_context output
#      ("The position is roughly equal in the opening.") was followed
#      by SafeExplainer.explain output ("Position is roughly equal.")
#      because both were appended.
#
# PR #169 deleted _build_context_block (the metadata-emitting helper)
# and stopped routing _safe_explainer.explain through
# _build_reply_deterministic.  These tests pin the user-visible reply
# contract so neither regression can sneak back in.
# ---------------------------------------------------------------------------


class TestDeterministicReplyNoMetadataLeak:
    """The deterministic Mode-2 reply must NOT expose LLM-prompt-shaped
    metadata (move counter, skill level, mistakes list, strengths
    list, training focus) — those are for the LLM prompt path only.
    The LLM path's own context block (built by _build_chat_llm) is
    untouched."""

    _METADATA_MARKERS: tuple[str, ...] = (
        "Player skill level:",
        "Recurring mistake areas:",
        "Strengths:",
        "Recent training focus:",
    )

    def _reply_with_full_profile(self) -> str:
        """Build a deterministic reply with every metadata source
        populated.  If any leak path is reintroduced, the assertions
        below catch the exact marker."""
        turns = _make_turns(("user", "What is my plan?"))
        profile = {
            "skill_estimate": "intermediate",
            "common_mistakes": [{"tag": "tactical_vision"}, {"tag": "time_management"}],
            "strengths": ["endgame_technique", "opening_principles"],
        }
        with _patch_llm_unavailable():
            reply = generate_chat_reply(
                _STARTING_FEN,
                turns,
                player_profile=profile,
                past_mistakes=["tactical_vision", "endgame_technique"],
                move_count=6,
            ).reply
        return reply

    def test_NO_METADATA_LEAK_known_markers(self):
        """Pin 34: no known metadata sentence appears in the user-
        visible reply.  Each marker is tested individually so a
        failure message names the exact leak."""
        reply = self._reply_with_full_profile()
        for marker in self._METADATA_MARKERS:
            assert marker not in reply, (
                f"User-visible chat reply contains the LLM-prompt-shaped "
                f"marker {marker!r}.  Metadata belongs in the LLM prompt "
                f"(built by _build_chat_llm), not in the deterministic "
                f"reply.  Reply: {reply!r}"
            )

    def test_NO_METADATA_LEAK_move_counter(self):
        """The move counter ("This is move 6 of the game.") was the
        most user-visible leak in the 2026-05-16 incident.  Pin it
        specifically — the marker is generic enough that a structural
        check ("This is move", "of the game") is more robust than
        the literal full sentence."""
        reply = self._reply_with_full_profile()
        assert "This is move" not in reply, (
            f"Deterministic reply contains the raw move-counter sentence: "
            f"{reply!r}.  This was the smoking-gun symptom of the PR #169 "
            f"regression — debug context was being concatenated into user "
            f"text."
        )
        assert "of the game." not in reply, (
            f"Deterministic reply still contains the move-counter trailer "
            f"'of the game.' — leak path partially reintroduced.  "
            f"Reply: {reply!r}"
        )


class TestDeterministicReplyNoDuplicateEvalSentence:
    """The deterministic reply must not contain two near-identical
    eval-state sentences.  Pre-PR-#169 the user saw 'The position is
    roughly equal in the opening. The position is stable. Position is
    roughly equal.' because _format_engine_context and SafeExplainer
    both fed the user-facing parts list."""

    def test_NO_DUPLICATE_EVAL_SENTENCE_equal_band(self):
        """Pin 35: the equal-band trigger phrase 'roughly equal'
        appears at most once in a deterministic reply for an equal
        position.  Catches the specific SafeExplainer-vs-
        _format_engine_context overlap that surfaced on-device."""
        turns = _make_turns(("user", "How should I play here?"))
        with _patch_llm_unavailable():
            reply = generate_chat_reply(_STARTING_FEN, turns).reply

        # Lowercase to absorb sentence-start capitalisation
        # differences between the two old code paths.
        occurrences = reply.lower().count("roughly equal")
        assert occurrences <= 1, (
            f"Deterministic reply repeats the 'roughly equal' phrase "
            f"{occurrences} times; pre-PR-#169 SafeExplainer + "
            f"_format_engine_context both emitted near-identical eval "
            f"sentences.  Reply: {reply!r}"
        )


# ---------------------------------------------------------------------------
# In-pipeline gate parity with validate_chat_response
#
# Issue (Mode-2 cousin of #129): the chat pipeline used to run only
# `_check_output` + `_validate_neg` inside `_build_chat_llm`.  The API
# boundary `validate_chat_response` runs structure + semantic on top.  A
# borderline LLM reply (eg "Your plan should be ..." for structure, or
# "slight advantage" on an equal-band position for semantic) slipped
# past the pipeline retry loop, escaped, and 500'd at the boundary —
# the client surfaced this as "Coach is offline" after a successful
# LLM call.
#
# These tests pin that the pipeline now runs every gate the boundary
# does, so borderline replies are either retried or fall through to the
# deterministic builder before reaching the route handler.
# ---------------------------------------------------------------------------


class TestPipelineBoundaryParity:
    def test_structure_violation_falls_back(self):
        """CHAT_STRUCT_RECOMMENDED — `_validate_struct` is wired into the pipeline.

        An LLM reply that presents a prescriptive "Recommended move:"
        section trips ``validate_mode_2_structure`` (FORBIDDEN_SECTIONS
        carries ``\\brecommended move\\b``), the retry loop catches the
        AssertionError, retry-hint is applied, the second attempt also
        fails (we keep returning the bad reply), and the call falls
        through to the deterministic path — which we assert does NOT
        contain "recommended move".

        Note: this used a "Plan:" header until 2026-06-07, when "plan"
        was fully retired from MOVE_ADVISORY_PATTERNS (the header word is
        harmless on its own).  "Recommended move:" is a still-blocked
        advisory-section form, so the *structure* gate is unambiguously
        what rejects it (the reply passes the negative gate cleanly).
        See _rules.MOVE_ADVISORY_PATTERNS and test_structure_plan_unlock.py.
        """
        turns = _make_turns(("user", "What should I do here?"))
        # Note: `_call_llm` is what `_build_chat_llm` invokes; patching
        # it (instead of `_build_chat_llm` itself) keeps the in-pipeline
        # validators in scope so structure rejection is what we observe.
        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(f"{_MODULE}._CHAT_RETRY_DELAY_SECONDS", 0),
            patch(
                f"{_MODULE}._call_llm",
                return_value="Recommended move: develop your pieces and fight for the centre.",
            ),
        ):
            result = generate_chat_reply(_STARTING_FEN, turns)
        lower = result.reply.lower()
        assert "recommended move" not in lower, (
            f"Deterministic fallback leaked the structure-forbidden section "
            f"'recommended move': {result.reply!r}"
        )
        assert result.reply.strip()

    def test_semantic_equal_band_advantage_falls_back(self):
        """CHAT_SEM_EQUAL — `_validate_sem` is wired into the pipeline.

        Starting position FEN → engine_signal has band == "equal".
        A reply that uses FORBIDDEN_EQUAL vocabulary ("slight advantage",
        "better", "initiative") trips ``validate_mode_2_semantic``;
        the retry loop catches ``Mode2Violation``, retry-hint is
        applied, and on exhaustion the call falls through to the
        deterministic builder.  The deterministic builder for an
        equal band uses the literal "equal" / "roughly equal" and
        never the FORBIDDEN tokens.
        """
        turns = _make_turns(("user", "What's the situation?"))
        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(f"{_MODULE}._CHAT_RETRY_DELAY_SECONDS", 0),
            patch(
                f"{_MODULE}._call_llm",
                return_value="You have a slight advantage and a clear initiative.",
            ),
        ):
            result = generate_chat_reply(_STARTING_FEN, turns)
        lower = result.reply.lower()
        for forbidden in ("slight advantage", "better", "initiative"):
            assert forbidden not in lower, (
                f"Deterministic fallback leaked the equal-band token '{forbidden}': {result.reply!r}"
            )
        assert result.reply.strip()

    def test_force_deterministic_skips_llm(self):
        """CHAT_FORCE_DET — handler safety-net flag bypasses the LLM.

        The /chat and /chat/stream handlers call `generate_chat_reply`
        with `force_deterministic=True` when the boundary validator
        rejects a pipeline reply.  Pin that the re-call never invokes
        the LLM, so a validator drift cannot 500 a second time on the
        same surface.
        """
        turns = _make_turns(("user", "Hi"))
        called = {"n": 0}

        def _spy(*args, **kwargs):
            called["n"] += 1
            return "should not be reached"

        with (
            patch(f"{_MODULE}._LLM_AVAILABLE", True),
            patch(f"{_MODULE}._call_llm", side_effect=_spy),
        ):
            result = generate_chat_reply(
                _STARTING_FEN, turns, force_deterministic=True
            )
        assert called["n"] == 0, (
            "force_deterministic=True must skip the LLM path entirely; "
            f"got _call_llm invoked {called['n']} time(s)"
        )
        assert result.mode == "CHAT_V1"
        assert result.reply.strip()

    def test_deterministic_mate_reply_is_second_person(self):
        """CHAT_MATE_PERSPECTIVE — the deterministic mate fallback frames the
        winner from the player's seat (always White in chat) as "you", aligned
        with the Mode-1 hint.  White delivers the mate here (value > 0), so the
        player is winning and the reply must not read third-person "white
        secures …"."""
        turns = _make_turns(("user", "Am I winning?"))
        sf = {
            "evaluation": {"type": "mate", "value": 1},
            "tactical_flags": [],
            "position_flags": [],
        }
        result = generate_chat_reply(
            _STARTING_FEN, turns, stockfish_json=sf, force_deterministic=True
        )
        assert "you secure the decisive outcome" in result.reply.lower(), result.reply
        assert "white secures" not in result.reply.lower(), result.reply


class TestStockfishSignal:
    """_chat_engine_signal uses a supplied Stockfish analysis for the TRUE eval,
    and falls back to material + deterministic flags when none is given."""

    def test_stockfish_json_drives_eval(self):
        from llm.seca.coach.chat_pipeline import _chat_engine_signal

        sf = {
            "evaluation": {"type": "mate", "value": 1},
            "tactical_flags": [],
            "position_flags": [],
        }
        sig = _chat_engine_signal(_STARTING_FEN, sf)
        assert sig["evaluation"]["type"] == "mate"
        assert sig["evaluation"]["side"] == "white"

    def test_no_stockfish_falls_back_to_material_and_flags(self):
        from llm.seca.coach.chat_pipeline import _chat_engine_signal

        sig = _chat_engine_signal(_STARTING_FEN, None)
        assert sig["evaluation"]["type"] == "cp"
        assert any(f.startswith("material:") for f in sig["position_flags"])


class TestTargetedRetryHint:
    """The retry hint names the exact validator-rejected token so the model
    rephrases and PASSES on retry, instead of re-failing the same way and
    exhausting to the robotic deterministic fallback (the 'how do I play a
    gambit?' → templated-reply complaint, 2026-07-08).  Does NOT weaken any
    validator — it steers the model toward a compliant, natural reply."""

    def test_equal_band_advantage_hint_names_the_word_and_offers_concept(self):
        from llm.rag.validators.mode_2_semantic import Mode2Violation
        from llm.seca.coach.chat_pipeline import _targeted_retry_hint

        hint = _targeted_retry_hint(
            Mode2Violation("Equal position described as advantage: 'winning'")
        )
        assert "winning" in hint
        assert "level" in hint.lower()
        # offers a natural, concept-level rephrase (not just "don't")
        assert "strong attack" in hint.lower() or "initiative" in hint.lower()
        assert "natural" in hint.lower()

    def test_invented_tactic_hint_steers_to_verb_form(self):
        from llm.rag.validators.mode_2_semantic import Mode2Violation
        from llm.seca.coach.chat_pipeline import _targeted_retry_hint

        # Only the BARE NOUN is blocked (whole-word), so the hint steers to
        # the verb/adjective form ("sacrificing"/"sacrificial") which passes
        # — the fix for a question that is ABOUT the motif ("when should I
        # sacrifice a piece?") where "give up a pawn" doesn't fit.
        hint = _targeted_retry_hint(
            Mode2Violation("Invented tactic without flag: 'sacrifice'")
        )
        assert "sacrificing" in hint.lower() and "sacrificial" in hint.lower()
        # a fork/pin question gets its own idea-preserving rephrase too
        fork = _targeted_retry_hint(Mode2Violation("Invented tactic without flag: 'fork'"))
        assert "two pieces" in fork.lower() or "forking" in fork.lower()

    def test_engine_speculative_hint_reinforces_coach_voice(self):
        from llm.rag.validators.mode_2_semantic import Mode2Violation
        from llm.seca.coach.chat_pipeline import _targeted_retry_hint

        hint = _targeted_retry_hint(Mode2Violation("Speculative language detected: 'engine'"))
        assert "engine" in hint.lower() and "coach" in hint.lower()

    def test_notation_hint_says_no_notation(self):
        from llm.seca.coach.chat_pipeline import _targeted_retry_hint

        hint = _targeted_retry_hint(
            AssertionError("Forbidden MODE-2 pattern detected: pattern `\b[KQRBN]?[a-h][1-8]\b`")
        )
        assert "notation" in hint.lower()
        assert "role" in hint.lower()

    def test_mate_hint_says_inevitable(self):
        from llm.rag.validators.mode_2_semantic import Mode2Violation
        from llm.seca.coach.chat_pipeline import _targeted_retry_hint

        hint = _targeted_retry_hint(Mode2Violation("Mate not described as forced/inevitable"))
        assert "inevitable" in hint.lower()

    def test_unknown_message_falls_back_to_generic_hint(self):
        from llm.seca.coach.chat_pipeline import _CHAT_RETRY_HINT, _targeted_retry_hint

        assert _targeted_retry_hint(AssertionError("something unrecognised")) == _CHAT_RETRY_HINT
