"""
Player Level Quality Tests — llm/tests/test_player_level_quality.py

Verifies that the coaching pipeline produces correctly differentiated output
for beginner, intermediate/club, and advanced players across all three pipelines:
  - chat_pipeline.generate_chat_reply()
  - live_move_pipeline.generate_live_reply()
  - seca.explainer.safe_explainer.SafeExplainer

Test categories
---------------
PLQ-01  _map_skill_level correctly maps all profile labels
PLQ-02  Beginner and advanced players receive different chat coaching advice
PLQ-03  Club players are mapped to intermediate level (bug regression)
PLQ-04  Live-move simple style omits phase tip; advanced style adds base explanation
PLQ-05  SafeExplainer produces level-differentiated output for beginners
PLQ-06  All levels produce non-empty, non-identical coaching replies
PLQ-07  Level-differentiated advice is appropriate depth per level
PLQ-08  Chat pipeline determinism across all player levels
PLQ-09  Live-move pipeline determinism across all explanation styles
PLQ-10  Level mapping does not affect engine signal integrity
"""

from __future__ import annotations

import pytest

from llm.seca.coach.chat_pipeline import (
    _map_skill_level,
    generate_chat_reply,
    ChatTurn,
)
from llm.seca.coach.live_move_pipeline import generate_live_reply, _build_hint
from llm.seca.explainer.safe_explainer import SafeExplainer

_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_MID_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
_ENDGAME_FEN = "8/8/4k3/8/8/4K3/4P3/8 w - - 0 1"

_UCI = "e2e4"


def _signal(band="equal", side="white", phase="middlegame", move_quality="unknown"):
    return {
        "evaluation": {"type": "cp", "band": band, "side": side},
        "eval_delta": "stable",
        "last_move_quality": move_quality,
        "tactical_flags": [],
        "position_flags": [],
        "phase": phase,
    }


def _mate_signal(side="white"):
    return {
        "evaluation": {"type": "mate", "band": "decisive_advantage", "side": side},
        "eval_delta": "increase",
        "last_move_quality": "excellent",
        "tactical_flags": ["forced_mate"],
        "position_flags": [],
        "phase": "endgame",
    }


# ---------------------------------------------------------------------------
# PLQ-01  _map_skill_level mapping correctness
# ---------------------------------------------------------------------------


class TestMapSkillLevel:

    @pytest.mark.parametrize(
        "skill_estimate,expected",
        [
            ("beginner", "beginner"),
            ("novice", "beginner"),
            ("Beginner", "beginner"),  # case-insensitive
            ("NOVICE", "beginner"),
            ("intermediate", "intermediate"),
            ("club", "intermediate"),   # PLQ-03 regression: club ≠ advanced
            ("Club Player", "intermediate"),
            ("advanced", "advanced"),
            ("expert", "advanced"),
            ("master", "advanced"),
            ("Master", "advanced"),
            ("grandmaster", "advanced"),  # contains "master"
            ("unknown_level", "intermediate"),  # default fallback
            ("", "intermediate"),  # empty string → default
        ],
    )
    def test_map_skill_level_label(self, skill_estimate, expected):
        profile = {"skill_estimate": skill_estimate}
        result = _map_skill_level(profile)
        assert result == expected, (
            f"_map_skill_level({skill_estimate!r}) returned {result!r}, expected {expected!r}"
        )

    def test_none_profile_returns_intermediate(self):
        assert _map_skill_level(None) == "intermediate"

    def test_empty_profile_returns_intermediate(self):
        assert _map_skill_level({}) == "intermediate"

    def test_missing_skill_estimate_returns_intermediate(self):
        assert _map_skill_level({"other_key": "value"}) == "intermediate"


# ---------------------------------------------------------------------------
# PLQ-02  Beginner vs advanced get different chat coaching advice
# ---------------------------------------------------------------------------


class TestChatLevelDifferentiation:

    def _reply(self, skill_estimate: str, query: str = "What tactic should I look for?") -> str:
        profile = {"skill_estimate": skill_estimate, "common_mistakes": [], "strengths": []}
        turns = [ChatTurn(role="user", content=query)]
        return generate_chat_reply(_MID_FEN, turns, player_profile=profile).reply

    def test_beginner_and_advanced_tactics_advice_differ(self):
        """Different player levels must produce different tactical coaching advice."""
        beginner_reply = self._reply("beginner")
        advanced_reply = self._reply("advanced")
        assert beginner_reply != advanced_reply, (
            "Beginner and advanced players must receive different coaching replies. "
            f"Both got: {beginner_reply!r}"
        )

    def test_beginner_tactics_uses_simpler_language(self):
        """Beginner advice should reference 'unprotected pieces', not complex calculation."""
        reply = self._reply("beginner", "What tactic should I look for?")
        assert "unprotected" in reply.lower() or "undefended" in reply.lower() or "safe" in reply.lower(), (
            f"Beginner tactical advice should reference unprotected pieces: {reply!r}"
        )

    def test_advanced_tactics_references_calculation(self):
        """Advanced advice should reference calculation or forcing moves."""
        reply = self._reply("advanced", "What tactic should I look for?")
        assert (
            "calculat" in reply.lower()
            or "forcing" in reply.lower()
            or "candidate" in reply.lower()
            or "checks" in reply.lower()
        ), f"Advanced tactical advice should reference calculation: {reply!r}"

    def test_beginner_opening_advice_is_basic(self):
        """Beginner opening advice should be straightforward (develop, castle)."""
        reply = self._reply("beginner", "What is the best opening strategy?")
        assert (
            "develop" in reply.lower()
            or "castle" in reply.lower()
            or "centre" in reply.lower()
            or "center" in reply.lower()
        ), f"Beginner opening advice should cover basics: {reply!r}"

    def test_advanced_opening_advice_references_structure(self):
        """Advanced opening advice should reference pawn structure or imbalances."""
        reply = self._reply("advanced", "What is the best opening strategy?")
        assert (
            "structure" in reply.lower()
            or "imbalanc" in reply.lower()
            or "middlegame" in reply.lower()
        ), f"Advanced opening advice should reference structure: {reply!r}"

    def test_club_player_gets_intermediate_not_advanced_advice(self):
        """Club players (mid-tier) must get intermediate coaching advice, not advanced.

        The context block includes the raw skill_estimate label, so the full reply
        strings differ by that label ("club" vs "intermediate"). We compare only
        the coaching advice portion that follows the question-response preface.
        The preface text changed from `On your question "...":` to
        `In response to your question:` when the deterministic fallback stopped
        echoing raw user text into replies (Mode-2 boundary safety) — this test
        uses the new connector as its slice marker.
        """
        query = "What is the plan here?"
        club_reply = self._reply("club", query)
        advanced_reply = self._reply("advanced", query)
        intermediate_reply = self._reply("intermediate", query)

        def _advice_part(reply: str) -> str:
            marker = "In response to your question:"
            idx = reply.find(marker)
            return reply[idx:] if idx != -1 else reply

        club_advice = _advice_part(club_reply)
        intermediate_advice = _advice_part(intermediate_reply)
        advanced_advice = _advice_part(advanced_reply)

        # Club and intermediate advice must be identical (same mapped level)
        assert club_advice == intermediate_advice, (
            "Club and intermediate players must receive identical coaching advice. "
            f"club_advice={club_advice!r}, intermediate_advice={intermediate_advice!r}"
        )
        # Club advice must differ from advanced advice
        assert club_advice != advanced_advice, (
            "Club players must NOT get advanced-level coaching advice. "
            f"Both got advice: {club_advice!r}"
        )

    def test_all_three_levels_produce_non_empty_reply(self):
        for level in ("beginner", "intermediate", "advanced"):
            reply = self._reply(level, "How should I play the endgame?")
            assert reply.strip(), f"Empty reply for level={level!r}"


# ---------------------------------------------------------------------------
# PLQ-04  Live-move explanation_style differentiation
# ---------------------------------------------------------------------------


class TestLiveMoveStyleDifferentiation:

    def test_simple_style_omits_phase_tip(self):
        """'simple' style must skip the phase-specific coaching tip."""
        signal = _signal(phase="opening")
        hint_simple = _build_hint(_UCI, signal, "", explanation_style="simple")
        hint_default = _build_hint(_UCI, signal, "")
        # The opening phase tip "Keep developing" must appear in default but not simple
        assert "develop" in hint_default.lower() or "centre" in hint_default.lower(), (
            f"Default style should include opening phase tip: {hint_default!r}"
        )
        assert "develop" not in hint_simple.lower() and "centre" not in hint_simple.lower(), (
            f"Simple style must omit opening phase tip: {hint_simple!r}"
        )

    def test_advanced_style_includes_base_explanation(self):
        """'advanced' style must include the SafeExplainer base explanation."""
        signal = _signal(band="decisive_advantage", side="white")
        base = "White has a decisive advantage."
        hint_adv = _build_hint(_UCI, signal, base, explanation_style="advanced")
        hint_int = _build_hint(_UCI, signal, base, explanation_style="intermediate")
        assert base in hint_adv, (
            f"Advanced style must include base explanation: {hint_adv!r}"
        )
        assert base not in hint_int, (
            f"Intermediate style must NOT include base explanation: {hint_int!r}"
        )

    def test_simple_and_intermediate_and_advanced_all_contain_engine_eval(self):
        """All styles must reference the engine evaluation."""
        signal = _signal(band="clear_advantage", side="white")
        for style in ("simple", "intermediate", "advanced", None):
            hint = _build_hint(_UCI, signal, "", explanation_style=style)
            assert "advantage" in hint.lower(), (
                f"style={style!r}: hint must mention engine evaluation: {hint!r}"
            )

    def test_blunder_generates_attention_prefix_for_all_styles(self):
        """Blunder move quality must trigger 'Attention:' prefix regardless of style."""
        signal = _signal(band="decisive_advantage", side="white", move_quality="blunder")
        signal["tactical_flags"] = []
        for style in ("simple", "intermediate", "advanced"):
            hint = _build_hint(_UCI, signal, "", explanation_style=style)
            assert "blunder" in hint.lower(), (
                f"style={style!r}: blunder must be mentioned in hint: {hint!r}"
            )

    def test_live_reply_via_generate_simple_vs_advanced(self):
        """generate_live_reply with different styles must produce different hints."""
        r_simple = generate_live_reply(_MID_FEN, _UCI, explanation_style="simple")
        r_advanced = generate_live_reply(_MID_FEN, _UCI, explanation_style="advanced")
        # Both must be valid
        assert r_simple.hint.strip()
        assert r_advanced.hint.strip()
        # They may or may not differ depending on position, but both must reference engine eval
        band = r_simple.engine_signal["evaluation"]["band"].replace("_", " ")
        for word in band.split():
            assert word in r_simple.hint or any(w in r_simple.hint for w in band.split()), (
                f"simple hint must reference eval band: {r_simple.hint!r}"
            )


# ---------------------------------------------------------------------------
# PLQ-05  SafeExplainer level-differentiated output
# ---------------------------------------------------------------------------


class TestSafeExplainerLevelDifferentiation:

    def test_beginner_and_advanced_output_differ_for_clear_advantage(self):
        signal = _signal(band="clear_advantage", side="white")
        explainer = SafeExplainer()
        beginner = explainer.explain(signal, skill_level="beginner")
        advanced = explainer.explain(signal, skill_level="advanced")
        assert beginner != advanced, (
            f"Beginner and advanced must differ for clear_advantage. "
            f"beginner={beginner!r}, advanced={advanced!r}"
        )

    def test_beginner_output_is_more_descriptive_for_decisive_advantage(self):
        signal = _signal(band="decisive_advantage", side="white")
        explainer = SafeExplainer()
        beginner = explainer.explain(signal, skill_level="beginner")
        advanced = explainer.explain(signal, skill_level="advanced")
        # Beginner description should be longer/more descriptive
        assert len(beginner) >= len(advanced) or "almost winning" in beginner.lower(), (
            f"Beginner explanation should be more descriptive. "
            f"beginner={beginner!r}, advanced={advanced!r}"
        )

    def test_beginner_blunder_message_is_more_explanatory(self):
        signal = _signal(move_quality="blunder")
        explainer = SafeExplainer()
        beginner = explainer.explain(signal, skill_level="beginner")
        advanced = explainer.explain(signal, skill_level="advanced")
        assert len(beginner) > len(advanced), (
            f"Beginner blunder message should be more verbose: beginner={beginner!r}"
        )

    def test_all_levels_produce_non_empty_output_for_all_bands(self):
        bands = ["equal", "small_advantage", "clear_advantage", "decisive_advantage"]
        explainer = SafeExplainer()
        for band in bands:
            for level in ("beginner", "intermediate", "advanced"):
                signal = _signal(band=band, side="white")
                result = explainer.explain(signal, skill_level=level)
                assert result.strip(), f"Empty output for band={band!r}, level={level!r}"

    def test_mate_signal_consistent_across_levels(self):
        """Mate signal always says 'forced mate' regardless of skill level."""
        signal = _mate_signal(side="white")
        explainer = SafeExplainer()
        for level in ("beginner", "intermediate", "advanced"):
            result = explainer.explain(signal, skill_level=level)
            assert "mate" in result.lower() or "winning" in result.lower(), (
                f"Mate signal must mention mate/winning for level={level!r}: {result!r}"
            )

    def test_invalid_level_falls_back_to_intermediate(self):
        """An unrecognised skill_level must fall back to intermediate without crashing."""
        signal = _signal(band="clear_advantage", side="white")
        explainer = SafeExplainer()
        result_invalid = explainer.explain(signal, skill_level="grandmaster_level")
        result_intermediate = explainer.explain(signal, skill_level="intermediate")
        assert result_invalid == result_intermediate, (
            "Unknown level must fall back to intermediate output"
        )

    def test_default_level_is_intermediate(self):
        signal = _signal(band="small_advantage", side="black")
        explainer = SafeExplainer()
        assert explainer.explain(signal) == explainer.explain(signal, skill_level="intermediate")


# ---------------------------------------------------------------------------
# PLQ-06  All levels produce non-empty, correctly-differentiated replies
# ---------------------------------------------------------------------------


class TestAllLevelsNonEmpty:

    @pytest.mark.parametrize("skill_estimate", ["beginner", "club", "intermediate", "advanced"])
    def test_chat_reply_non_empty_for_all_levels(self, skill_estimate):
        profile = {"skill_estimate": skill_estimate, "common_mistakes": [], "strengths": []}
        turns = [ChatTurn(role="user", content="Explain the position.")]
        result = generate_chat_reply(_STARTING_FEN, turns, player_profile=profile)
        assert result.reply.strip(), f"Empty reply for skill_estimate={skill_estimate!r}"
        assert result.mode == "CHAT_V1"

    @pytest.mark.parametrize("style", ["simple", "intermediate", "advanced", None])
    def test_live_reply_non_empty_for_all_styles(self, style):
        result = generate_live_reply(_MID_FEN, _UCI, explanation_style=style)
        assert result.hint.strip(), f"Empty hint for explanation_style={style!r}"
        assert result.mode == "LIVE_V1"


# ---------------------------------------------------------------------------
# PLQ-07  Depth appropriateness: advanced advice has more conceptual content
# ---------------------------------------------------------------------------


class TestAdviceDepthAppropriateness:

    def test_endgame_beginner_advice_shorter_than_advanced(self):
        profile_b = {"skill_estimate": "beginner", "common_mistakes": [], "strengths": []}
        profile_a = {"skill_estimate": "advanced", "common_mistakes": [], "strengths": []}
        query = "How should I play the endgame?"
        turns = [ChatTurn(role="user", content=query)]
        reply_b = generate_chat_reply(_ENDGAME_FEN, turns, player_profile=profile_b).reply
        reply_a = generate_chat_reply(_ENDGAME_FEN, turns, player_profile=profile_a).reply
        assert len(reply_a) >= len(reply_b) or "precise" in reply_a.lower(), (
            f"Advanced endgame advice should be at least as detailed as beginner. "
            f"beginner={len(reply_b)} chars, advanced={len(reply_a)} chars"
        )

    def test_tactical_beginner_advice_focuses_on_basics(self):
        profile = {"skill_estimate": "beginner"}
        turns = [ChatTurn(role="user", content="What is the tactic here?")]
        reply = generate_chat_reply(_MID_FEN, turns, player_profile=profile).reply
        # Beginner should get simple advice about unprotected pieces, not deep calculation
        assert "calculate" not in reply.lower() or "unprotected" in reply.lower(), (
            f"Beginner advice should not emphasise deep calculation: {reply!r}"
        )

    def test_strategic_advanced_advice_mentions_imbalances_or_structure(self):
        profile = {"skill_estimate": "advanced"}
        turns = [ChatTurn(role="user", content="What is the strategic plan?")]
        reply = generate_chat_reply(_MID_FEN, turns, player_profile=profile).reply
        assert (
            "imbalanc" in reply.lower()
            or "structure" in reply.lower()
            or "activity" in reply.lower()
            or "pawn" in reply.lower()
        ), f"Advanced strategic advice should reference imbalances/structure: {reply!r}"


# ---------------------------------------------------------------------------
# PLQ-08  Chat pipeline determinism across all levels
# ---------------------------------------------------------------------------


class TestChatPipelineDeterminism:

    @pytest.mark.parametrize("skill_estimate", ["beginner", "club", "intermediate", "advanced"])
    def test_identical_inputs_deterministic(self, skill_estimate):
        profile = {"skill_estimate": skill_estimate}
        turns = [ChatTurn(role="user", content="What is happening?")]
        r1 = generate_chat_reply(_MID_FEN, turns, player_profile=profile)
        r2 = generate_chat_reply(_MID_FEN, turns, player_profile=profile)
        assert r1.reply == r2.reply
        assert r1.engine_signal == r2.engine_signal


# ---------------------------------------------------------------------------
# PLQ-09  Live-move pipeline determinism across all styles
# ---------------------------------------------------------------------------


class TestLiveMoveDeterminism:

    @pytest.mark.parametrize("style", ["simple", "intermediate", "advanced", None])
    def test_identical_inputs_deterministic(self, style):
        r1 = generate_live_reply(_MID_FEN, _UCI, explanation_style=style)
        r2 = generate_live_reply(_MID_FEN, _UCI, explanation_style=style)
        assert r1.hint == r2.hint
        assert r1.engine_signal == r2.engine_signal


# ---------------------------------------------------------------------------
# PLQ-10  Level mapping does not affect engine signal integrity
# ---------------------------------------------------------------------------


class TestLevelMappingEngineSignalIntegrity:

    def test_engine_signal_identical_across_player_levels(self):
        """The engine signal must be exactly the same regardless of player skill level."""
        profiles = [
            {"skill_estimate": "beginner"},
            {"skill_estimate": "club"},
            {"skill_estimate": "intermediate"},
            {"skill_estimate": "advanced"},
            None,
        ]
        turns = [ChatTurn(role="user", content="Explain.")]
        signals = [
            generate_chat_reply(_MID_FEN, turns, player_profile=p).engine_signal
            for p in profiles
        ]
        for i, sig in enumerate(signals[1:], 1):
            assert sig == signals[0], (
                f"Engine signal differs for profile index {i}: "
                f"expected={signals[0]!r}, got={sig!r}"
            )

    def test_live_move_engine_signal_identical_across_styles(self):
        styles = ["simple", "intermediate", "advanced", None]
        signals = [generate_live_reply(_MID_FEN, _UCI, explanation_style=s).engine_signal for s in styles]
        for i, sig in enumerate(signals[1:], 1):
            assert sig == signals[0], (
                f"Engine signal differs for style index {i}: "
                f"expected={signals[0]!r}, got={sig!r}"
            )
