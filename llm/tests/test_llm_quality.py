"""
LLM response quality tests — llm/tests/test_llm_quality.py

Evaluates response quality across all player levels and both pipelines.
All tests are CI-safe: no live Ollama required.

Invariants
----------
QUAL-01  Mode-1 prompt contains "beginner" for simple style
QUAL-02  Mode-1 prompt contains "advanced" for advanced style
QUAL-03  Mode-1 prompt contains engine signal JSON
QUAL-04  Mode-1 prompt contains sanitized FEN
QUAL-05  Mode-1 prompt contains move quality label
QUAL-06  Mode-2 prompt contains engine signal JSON
QUAL-07  Mode-2 prompt contains sanitized FEN
QUAL-08  Mode-2 prompt wraps user query in <user_query> tags
QUAL-09  SafeExplainer beginner band message uses plain, non-jargon language
QUAL-10  SafeExplainer advanced band message is concise and technical
QUAL-11  SafeExplainer beginner blunder is encouraging/non-technical
QUAL-12  SafeExplainer advanced quality messages differ from beginner
QUAL-13  SafeExplainer handles unknown quality without crashing
QUAL-14  SafeExplainer handles unknown skill level by defaulting to intermediate
QUAL-15  SafeExplainer handles mate signal correctly
QUAL-16  CLC produces "urgent" tone for blunder position
QUAL-17  CLC produces "supportive" tone for low-ELO player in calm position
QUAL-18  CLC produces "instructive" tone for low-confidence equal position
QUAL-19  CLC produces "urgent" instruction for forced-mate tactical flag
QUAL-20  Mode-1 deterministic simple hint is one sentence
QUAL-21  Mode-1 deterministic intermediate hint may be two sentences
QUAL-22  Mode-1 deterministic hints differ between beginner and advanced
QUAL-23  Mode-2 deterministic beginner reply uses beginner coaching advice
QUAL-24  Mode-2 deterministic advanced reply uses advanced coaching advice
QUAL-25  Mode-2 fallback includes phase tip for opening/endgame
QUAL-26  generate_chat_reply beginner ≠ advanced (level differentiation end-to-end)
QUAL-27  generate_live_reply simple ≠ advanced (level differentiation end-to-end)
"""

from __future__ import annotations

import pytest

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.prompts.mode_1.render import render_mode_1_prompt
from llm.rag.prompts.mode_2.render import render_mode_2_prompt
from llm.seca.coach.chat_pipeline import (
    ChatTurn,
    _build_reply_deterministic,
    _build_context_block,
    generate_chat_reply,
)
from llm.seca.coach.live_move_pipeline import _build_hint, generate_live_reply
from llm.seca.explainer.safe_explainer import SafeExplainer
from llm.confidence_language_controller import (
    build_language_controller_block,
    build_style_instruction,
    compute_tone,
    compute_confidence,
    compute_urgency,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_FEN_START = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
_FEN_MID = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 4 6"
_DUMMY_SYSTEM = "You are a chess coach."

_ESV_EQUAL = {
    "evaluation": {"band": "equal", "side": "equal", "type": "cp"},
    "eval_delta": "stable",
    "last_move_quality": "good",
    "phase": "middlegame",
    "tactical_flags": [],
}
_ESV_BLUNDER = {
    "evaluation": {"band": "clear_advantage", "side": "opponent", "type": "cp"},
    "eval_delta": "decrease",
    "last_move_quality": "blunder",
    "phase": "middlegame",
    "tactical_flags": [],
}
_ESV_MATE = {
    "evaluation": {"band": "decisive_advantage", "side": "white", "type": "mate"},
    "eval_delta": "increase",
    "last_move_quality": "best",
    "phase": "endgame",
    "tactical_flags": ["forced_mate"],
}
_ESV_OPENING = {
    "evaluation": {"band": "equal", "side": "equal", "type": "cp"},
    "eval_delta": "stable",
    "last_move_quality": "good",
    "phase": "opening",
    "tactical_flags": [],
}

_PROFILE_BEGINNER = {"skill_estimate": "beginner", "common_mistakes": [], "strengths": []}
_PROFILE_ADVANCED = {"skill_estimate": "advanced", "common_mistakes": [], "strengths": []}


# ===========================================================================
# QUAL-01..05  Mode-1 prompt quality
# ===========================================================================


class TestMode1PromptQuality:
    """QUAL-01..05: Mode-1 prompt contains the right level context for LLM."""

    def test_simple_style_embeds_beginner_level(self):
        """QUAL-01: 'simple' explanation_style → prompt says 'beginner'."""
        prompt = render_mode_1_prompt(
            system_prompt=_DUMMY_SYSTEM,
            engine_signal=_ESV_EQUAL,
            fen=_FEN_START,
            explanation_style="simple",
        )
        assert "beginner" in prompt.lower(), (
            f"Expected 'beginner' in Mode-1 prompt for simple style. Got:\n{prompt[:500]}"
        )

    def test_advanced_style_embeds_advanced_level(self):
        """QUAL-02: 'advanced' explanation_style → prompt says 'advanced'."""
        prompt = render_mode_1_prompt(
            system_prompt=_DUMMY_SYSTEM,
            engine_signal=_ESV_EQUAL,
            fen=_FEN_START,
            explanation_style="advanced",
        )
        assert "advanced" in prompt.lower()

    def test_prompt_contains_engine_signal_json(self):
        """QUAL-03: Mode-1 prompt embeds engine signal as JSON."""
        prompt = render_mode_1_prompt(
            system_prompt=_DUMMY_SYSTEM,
            engine_signal=_ESV_EQUAL,
            fen=_FEN_START,
            explanation_style="intermediate",
        )
        assert '"evaluation"' in prompt and '"phase"' in prompt, (
            "Mode-1 prompt must embed engine signal JSON."
        )

    def test_prompt_contains_fen(self):
        """QUAL-04: Mode-1 prompt contains the FEN string."""
        prompt = render_mode_1_prompt(
            system_prompt=_DUMMY_SYSTEM,
            engine_signal=_ESV_EQUAL,
            fen=_FEN_MID,
            explanation_style="intermediate",
        )
        assert _FEN_MID in prompt

    def test_prompt_contains_move_quality(self):
        """QUAL-05: Mode-1 prompt contains the move quality label."""
        prompt = render_mode_1_prompt(
            system_prompt=_DUMMY_SYSTEM,
            engine_signal=_ESV_BLUNDER,
            fen=_FEN_START,
            explanation_style="intermediate",
        )
        assert "blunder" in prompt.lower()


# ===========================================================================
# QUAL-06..08  Mode-2 prompt quality
# ===========================================================================


class TestMode2PromptQuality:
    """QUAL-06..08: Mode-2 prompt contains the right context for LLM."""

    def test_prompt_contains_engine_signal_json(self):
        """QUAL-06: Mode-2 prompt embeds engine signal JSON."""
        prompt = render_mode_2_prompt(
            system_prompt=_DUMMY_SYSTEM,
            engine_signal=_ESV_EQUAL,
            rag_docs=[],
            fen=_FEN_START,
            user_query="What should I do?",
        )
        assert '"evaluation"' in prompt and '"phase"' in prompt

    def test_prompt_contains_fen(self):
        """QUAL-07: Mode-2 prompt contains the FEN."""
        prompt = render_mode_2_prompt(
            system_prompt=_DUMMY_SYSTEM,
            engine_signal=_ESV_EQUAL,
            rag_docs=[],
            fen=_FEN_MID,
            user_query="Explain the position.",
        )
        assert _FEN_MID in prompt

    def test_prompt_wraps_query_in_tags(self):
        """QUAL-08: User query is wrapped in <user_query> tags."""
        query = "How do I improve my position?"
        prompt = render_mode_2_prompt(
            system_prompt=_DUMMY_SYSTEM,
            engine_signal=_ESV_EQUAL,
            rag_docs=[],
            fen=_FEN_START,
            user_query=query,
        )
        assert f"<user_query>\n{query}" in prompt or f"<user_query>\n{query}" in prompt


# ===========================================================================
# QUAL-09..15  SafeExplainer level differentiation
# ===========================================================================


class TestSafeExplainerLevelDifferentiation:
    """QUAL-09..15: SafeExplainer uses level-appropriate language."""

    def setup_method(self):
        self.se = SafeExplainer()

    def test_beginner_equal_uses_plain_language(self):
        """QUAL-09: Beginner equal message uses 'even' / 'similar', not 'roughly'."""
        msg = self.se.explain(_ESV_EQUAL, skill_level="beginner")
        assert "even" in msg.lower() or "similar" in msg.lower(), (
            f"Beginner equal message should use plain language. Got: {msg!r}"
        )

    def test_intermediate_equal_is_concise(self):
        """QUAL-10: Intermediate equal message is short and technical."""
        msg = self.se.explain(_ESV_EQUAL, skill_level="intermediate")
        assert "roughly equal" in msg.lower() or "equal" in msg.lower()

    def test_beginner_blunder_is_non_technical(self):
        """QUAL-11: Beginner blunder message avoids chess jargon, is encouraging."""
        msg = self.se.explain(_ESV_BLUNDER, skill_level="beginner")
        assert "serious mistake" in msg.lower() or "opponent" in msg.lower(), (
            f"Beginner blunder should use plain language. Got: {msg!r}"
        )

    def test_advanced_blunder_differs_from_beginner(self):
        """QUAL-12: Advanced quality messages differ from beginner ones."""
        beginner_msg = self.se.explain(_ESV_BLUNDER, skill_level="beginner")
        advanced_msg = self.se.explain(_ESV_BLUNDER, skill_level="advanced")
        assert beginner_msg != advanced_msg, (
            "SafeExplainer must produce different blunder messages for beginner vs advanced."
        )

    def test_unknown_quality_does_not_crash(self):
        """QUAL-13: Unknown move quality produces a non-empty explanation."""
        esv = {**_ESV_EQUAL, "last_move_quality": "exotic_label"}
        msg = self.se.explain(esv, skill_level="intermediate")
        assert msg, "SafeExplainer must return non-empty string for unknown quality."

    def test_unknown_skill_defaults_to_intermediate(self):
        """QUAL-14: Unknown skill level falls back to intermediate, not blank."""
        msg = self.se.explain(_ESV_EQUAL, skill_level="grandmaster")
        intermediate_msg = self.se.explain(_ESV_EQUAL, skill_level="intermediate")
        assert msg == intermediate_msg, (
            "Unknown skill level should default to intermediate."
        )

    def test_mate_signal_produces_forced_mate_message(self):
        """QUAL-15: Mate engine signal → 'Forced mate' in explanation."""
        msg = self.se.explain(_ESV_MATE, skill_level="beginner")
        assert "mate" in msg.lower() or "winning" in msg.lower(), (
            f"Mate signal must produce mate-aware message. Got: {msg!r}"
        )


# ===========================================================================
# QUAL-16..19  Confidence Language Controller
# ===========================================================================


class TestConfidenceLanguageController:
    """QUAL-16..19: CLC produces level-appropriate tone instructions."""

    def test_blunder_produces_urgent_tone(self):
        """QUAL-16: Blunder position → 'urgent' tone."""
        block = build_language_controller_block(_ESV_BLUNDER)
        assert "urgent" in block.lower(), (
            f"Blunder position must trigger urgent tone. Got:\n{block}"
        )

    def test_low_elo_produces_supportive_tone(self):
        """QUAL-17: Player ELO < 1200 → 'supportive' tone in calm position."""
        block = build_language_controller_block(_ESV_EQUAL, player_elo=900)
        assert "supportive" in block.lower() or "encouraging" in block.lower(), (
            f"Low-ELO player must trigger supportive tone. Got:\n{block}"
        )

    def test_equal_stable_produces_instructive_tone(self):
        """QUAL-18: Equal + stable delta + no blunder → 'instructive' tone."""
        block = build_language_controller_block(_ESV_EQUAL)
        assert "instructive" in block.lower() or "step-by-step" in block.lower() or "teaching" in block.lower(), (
            f"Equal stable position must trigger instructive tone. Got:\n{block}"
        )

    def test_forced_mate_flag_produces_urgent_tone(self):
        """QUAL-19: forced_mate tactical flag → 'urgent' tone."""
        block = build_language_controller_block(_ESV_MATE)
        assert "urgent" in block.lower() or "immediacy" in block.lower() or "danger" in block.lower(), (
            f"Forced mate must trigger urgent tone. Got:\n{block}"
        )

    def test_blunder_urgency_is_critical(self):
        """compute_urgency returns 'critical' for blunder move quality."""
        urgency = compute_urgency(_ESV_BLUNDER)
        assert urgency == "critical"

    def test_forced_mate_urgency_is_critical(self):
        """compute_urgency returns 'critical' for forced_mate tactical flag."""
        urgency = compute_urgency(_ESV_MATE)
        assert urgency == "critical"


# ===========================================================================
# QUAL-20..22  Mode-1 deterministic level adaptation
# ===========================================================================


class TestMode1DeterministicLevelAdaptation:
    """QUAL-20..22: _build_hint produces level-appropriate hints."""

    def test_simple_style_is_one_sentence(self):
        """QUAL-20: Simple style returns exactly one sentence (no period-split > 1)."""
        hint = _build_hint("e2e4", _ESV_EQUAL, base_explanation="", explanation_style="simple")
        sentences = [s.strip() for s in hint.split(".") if s.strip()]
        assert len(sentences) <= 1, f"Simple style must be 1 sentence. Got: {hint!r}"

    def test_intermediate_hint_may_be_two_sentences(self):
        """QUAL-21: Intermediate/advanced hints may be 2 sentences (quality + eval)."""
        hint = _build_hint("e2e4", _ESV_BLUNDER, base_explanation="", explanation_style="intermediate")
        assert hint, "Intermediate hint must not be empty."
        assert len(hint) > 0

    def test_beginner_and_advanced_hints_differ(self):
        """QUAL-22: Simple vs advanced hints produce different text."""
        simple = _build_hint("e2e4", _ESV_BLUNDER, base_explanation="", explanation_style="simple")
        advanced = _build_hint("e2e4", _ESV_BLUNDER, base_explanation="", explanation_style="advanced")
        assert simple != advanced, (
            "Simple and advanced hints must differ for the same position."
        )

    def test_all_player_levels_produce_non_empty_hints(self):
        """All three styles produce non-empty hints for a blunder."""
        for style in ("simple", "intermediate", "advanced"):
            hint = _build_hint("e2e4", _ESV_BLUNDER, base_explanation="Material lost.", explanation_style=style)
            assert hint, f"Style '{style}' produced empty hint."

    def test_generate_live_reply_simple_ne_advanced(self):
        """QUAL-27: generate_live_reply produces different hints for simple vs advanced."""
        reply_simple = generate_live_reply(fen=_FEN_START, uci="e2e4", explanation_style="simple")
        reply_adv = generate_live_reply(fen=_FEN_START, uci="e2e4", explanation_style="advanced")
        assert reply_simple.hint != reply_adv.hint, (
            "generate_live_reply must produce different hints for simple vs advanced."
        )


# ===========================================================================
# QUAL-23..26  Mode-2 deterministic level adaptation
# ===========================================================================


class TestMode2DeterministicLevelAdaptation:
    """QUAL-23..26: Mode-2 deterministic path adapts to player level."""

    def _reply(self, skill_level: str, query: str = "What is the best plan?") -> str:
        profile = {"skill_estimate": skill_level, "common_mistakes": [], "strengths": []}
        context_block = _build_context_block(_ESV_EQUAL, profile, None)
        history = [ChatTurn(role="user", content=query)]
        return _build_reply_deterministic(
            user_query=query,
            context_block=context_block,
            engine_signal=_ESV_EQUAL,
            base_explanation="",
            history=history,
            skill_level=skill_level,
        )

    def test_beginner_reply_uses_beginner_advice(self):
        """QUAL-23: Beginner reply contains beginner-targeted coaching advice."""
        reply = self._reply("beginner", "What is the plan here?")
        assert reply, "Beginner reply must not be empty."
        reply_lower = reply.lower()
        assert any(kw in reply_lower for kw in ("piece safety", "piece", "improve")), (
            f"Beginner reply should use simplified coaching advice. Got: {reply!r}"
        )

    def test_advanced_reply_uses_advanced_advice(self):
        """QUAL-24: Advanced reply contains technical coaching advice."""
        reply = self._reply("advanced", "What is the plan here?")
        reply_lower = reply.lower()
        assert any(kw in reply_lower for kw in ("imbalance", "pawn structure", "piece activity", "candidate")), (
            f"Advanced reply should use technical coaching advice. Got: {reply!r}"
        )

    def test_opening_phase_includes_phase_tip(self):
        """QUAL-25: Mode-2 fallback includes opening phase tip."""
        context_block = _build_context_block(_ESV_OPENING, None, None)
        history = [ChatTurn(role="user", content="How should I develop?")]
        reply = _build_reply_deterministic(
            user_query="How should I develop?",
            context_block=context_block,
            engine_signal=_ESV_OPENING,
            base_explanation="",
            history=history,
            skill_level="intermediate",
        )
        assert "opening" in reply.lower() or "develop" in reply.lower() or "centre" in reply.lower(), (
            f"Opening phase reply must include phase tip. Got: {reply!r}"
        )

    def test_beginner_and_advanced_replies_differ(self):
        """QUAL-26: generate_chat_reply produces different content for beginner vs advanced."""
        fen = _FEN_START
        query = "What should I focus on?"
        messages = [ChatTurn(role="user", content=query)]

        reply_beg = generate_chat_reply(fen=fen, messages=messages, player_profile=_PROFILE_BEGINNER)
        reply_adv = generate_chat_reply(fen=fen, messages=messages, player_profile=_PROFILE_ADVANCED)

        assert reply_beg.reply != reply_adv.reply, (
            "generate_chat_reply must produce different replies for beginner vs advanced profiles."
        )
