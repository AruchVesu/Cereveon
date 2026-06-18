"""
Regression tests for the 2026-05-15 Mode-1 silent-500 incident.

Context
-------
Both ``llm.seca.coach.live_move_pipeline._build_hint`` and
``llm.seca.coach.chat_pipeline._format_engine_context`` produce the
mate-condition sentence for the deterministic fallback path.  Prior to
PR #167 they emitted:

    "This is a forced mate — {side} secures the decisive outcome."

The comments above each line explained that ``"forced"`` was deliberate
because ``validate_mode_2_semantic`` REQUIRES one of
``MATE_INEVITABILITY_SEMANTIC = ("inevitable", "forced")`` to appear
when the ESV says mate.  What the author missed is that the **lexical**
gate ``validate_mode_2_negative`` independently forbids the two-word
phrase ``\bforce(?:d)? mate\b`` via ``MATE_CLAIM_PATTERNS`` — and the
deterministic sentence contains exactly that adjacent pair.

Production effect (caught on-device 2026-05-15 in a real game,
request_ids ``966ad533d3514be59d3c35fa94f1e01d`` +
``090beaf0be534705a8c7f4af8506f0fb``):

  1. The position approaches mate; LLM tries 3× and trips the same
     lexical forbid each attempt.  Pipeline emits the deterministic
     fallback.
  2. The deterministic fallback ALSO contains "forced mate".
  3. The boundary re-validator at ``server.py:1357`` rejects it,
     intentionally surfacing this as a structural bug → 500.
  4. The Android client renders nothing for that turn — the user
     perceives it as "Mode-1 sometimes doesn't give a comment".

PR #167 switches the phrasing to ``"Mate is inevitable — ..."`` which
satisfies the semantic require (single-word ``"inevitable"``) without
triggering the lexical forbid (no ``"force(d) mate"`` adjacent pair).

Pinned invariants
-----------------
 1. LIVE_DETERMINISTIC_MATE_PASSES_NEGATIVE
        ``_build_hint`` mate output passes ``validate_mode_2_negative``.
 2. CHAT_DETERMINISTIC_MATE_PASSES_NEGATIVE
        ``_format_engine_context`` mate output passes
        ``validate_mode_2_negative``.
 3. LIVE_DETERMINISTIC_MATE_PASSES_SEMANTIC
        Live-move mate output passes ``validate_mode_2_semantic`` with
        the matching ESV (``eval_type="mate"``).
 4. CHAT_DETERMINISTIC_MATE_PASSES_SEMANTIC
        Chat mate output passes ``validate_mode_2_semantic`` likewise.
 5. NO_FORBIDDEN_MATE_PHRASE_IN_DETERMINISTIC
        Neither pipeline's deterministic mate output contains any
        token in ``MATE_CLAIM_PATTERNS`` (catches drift — e.g. a
        future refactor accidentally re-introducing ``"forced mate"``
        as a phrase fragment).
 6. VALIDATOR_VOCAB_NON_CONTRADICTORY
        For each token in ``MATE_INEVITABILITY_SEMANTIC`` (the
        single-word vocab the semantic gate REQUIRES), no
        ``MATE_CLAIM_PATTERNS`` regex matches that bare word — i.e.
        you can always satisfy the require without immediately
        tripping the forbid.  The original bug was a phrase-level
        collision (``"forced mate"``), not a word-level one, so this
        check passes today; it locks in the invariant against future
        widening of the lexical forbid to the bare word.
"""

from __future__ import annotations

import re

import pytest

from llm.rag.validators._rules import (
    MATE_CLAIM_PATTERNS,
    MATE_INEVITABILITY_SEMANTIC,
)
from llm.rag.validators.mode_2_negative import validate_mode_2_negative
from llm.rag.validators.mode_2_semantic import validate_mode_2_semantic
from llm.seca.coach.chat_pipeline import _format_engine_context
from llm.seca.coach.live_move_pipeline import _build_hint


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_MATE_ENGINE_SIGNAL = {
    "evaluation": {"type": "mate", "band": "decisive_white", "side": "White"},
    "last_move_quality": "blunder",
    "phase": "endgame",
}


# ---------------------------------------------------------------------------
# 1–2  Lexical-negative gate
# ---------------------------------------------------------------------------


class TestDeterministicMatePassesLexicalNegative:
    """The deterministic mate phrasing must not contain any forbidden
    Mode-2 lexical pattern.  Before PR #167 it contained
    ``"forced mate"`` which matched ``\\bforce(?:d)? mate\\b`` and
    500'd /live/move on every mate-ending game."""

    def test_LIVE_DETERMINISTIC_MATE_PASSES_NEGATIVE(self):
        """Pin 1: live-move deterministic mate output passes
        validate_mode_2_negative."""
        hint = _build_hint(
            uci="h7h8",
            engine_signal=_MATE_ENGINE_SIGNAL,
            base_explanation="",
            explanation_style="intermediate",
        )
        validate_mode_2_negative(hint)  # raises AssertionError on failure

    def test_CHAT_DETERMINISTIC_MATE_PASSES_NEGATIVE(self):
        """Pin 2: chat deterministic mate output passes
        validate_mode_2_negative."""
        eval_sentence = _format_engine_context(_MATE_ENGINE_SIGNAL)
        validate_mode_2_negative(eval_sentence)


# ---------------------------------------------------------------------------
# 3–4  Semantic-positive gate
# ---------------------------------------------------------------------------


class TestDeterministicMatePassesSemantic:
    """The deterministic mate phrasing must include the semantic
    decisiveness vocabulary (``"inevitable"`` or ``"forced"`` per
    ``MATE_INEVITABILITY_SEMANTIC``) so it survives the semantic gate
    that's gated on ``eval_type == "mate"``."""

    def test_LIVE_DETERMINISTIC_MATE_PASSES_SEMANTIC(self):
        """Pin 3: live-move deterministic mate output passes
        validate_mode_2_semantic with ``eval_type="mate"``."""
        hint = _build_hint(
            uci="h7h8",
            engine_signal=_MATE_ENGINE_SIGNAL,
            base_explanation="",
            explanation_style="intermediate",
        )
        validate_mode_2_semantic(hint, _MATE_ENGINE_SIGNAL)

    def test_CHAT_DETERMINISTIC_MATE_PASSES_SEMANTIC(self):
        """Pin 4: chat deterministic mate output passes
        validate_mode_2_semantic likewise."""
        eval_sentence = _format_engine_context(_MATE_ENGINE_SIGNAL)
        validate_mode_2_semantic(eval_sentence, _MATE_ENGINE_SIGNAL)


# ---------------------------------------------------------------------------
# 5  Drift guard — explicit forbidden-phrase rejection
# ---------------------------------------------------------------------------


class TestNoForbiddenMatePhraseInDeterministic:
    """If a future refactor accidentally re-introduces ``"forced mate"``
    (or any other MATE_CLAIM phrase) into the deterministic mate
    output, this test fails loudly and immediately — independent of
    the validator stack."""

    @pytest.mark.parametrize("pattern", MATE_CLAIM_PATTERNS)
    def test_live_deterministic_mate_does_not_match_pattern(self, pattern):
        hint = _build_hint(
            uci="h7h8",
            engine_signal=_MATE_ENGINE_SIGNAL,
            base_explanation="",
            explanation_style="intermediate",
        )
        assert not re.search(pattern, hint, re.IGNORECASE), (
            f"Live-move deterministic mate output contains the forbidden "
            f"MATE_CLAIM pattern `{pattern}` — this would 500 /live/move "
            f"at the boundary re-validator.  Sentence: {hint!r}"
        )

    @pytest.mark.parametrize("pattern", MATE_CLAIM_PATTERNS)
    def test_chat_deterministic_mate_does_not_match_pattern(self, pattern):
        eval_sentence = _format_engine_context(_MATE_ENGINE_SIGNAL)
        assert not re.search(pattern, eval_sentence, re.IGNORECASE), (
            f"Chat deterministic mate output contains the forbidden "
            f"MATE_CLAIM pattern `{pattern}`.  Sentence: {eval_sentence!r}"
        )


# ---------------------------------------------------------------------------
# 6  Validator vocab non-contradiction
# ---------------------------------------------------------------------------


class TestValidatorVocabNonContradictory:
    """For each single-word token the semantic gate REQUIRES, no
    lexical forbid regex matches that bare word.  Today the failing
    case in production was a phrase-level collision (``"forced mate"``
    matching ``\\bforce(?:d)? mate\\b``), not a word-level one — bare
    ``"forced"`` and bare ``"inevitable"`` both pass the lexical
    filter.  This pin catches the worst kind of validator drift: a
    future widening of MATE_CLAIM_PATTERNS to include bare
    ``"forced"`` would make EVERY mate-state output unsatisfiable,
    permanently 500-ing /live/move and /chat for any mate-ending
    game."""

    @pytest.mark.parametrize("required", MATE_INEVITABILITY_SEMANTIC)
    def test_required_token_does_not_match_any_mate_claim_pattern(
        self, required
    ):
        # Format as a standalone word to catch \b boundary-anchored
        # patterns that would only match the token in isolation
        # (which is the most user-friendly form to emit it).
        probe = f"The mate is {required}."
        for pattern in MATE_CLAIM_PATTERNS:
            assert not re.search(pattern, probe, re.IGNORECASE), (
                f"Validator vocab contradiction: the semantic gate "
                f"requires `{required}` when eval_type=='mate', but the "
                f"lexical gate `MATE_CLAIM_PATTERNS` rejects the probe "
                f"sentence containing it via pattern `{pattern}`.  This "
                f"makes every mate-state output unsatisfiable and 500s "
                f"/live/move + falls through chat to deterministic — "
                f"exactly the 2026-05-15 incident."
            )


# ---------------------------------------------------------------------------
# 7  Player-perspective framing — second person, still gate-safe
# ---------------------------------------------------------------------------


class TestDeterministicMatePlayerPerspective:
    """When the player's colour is known, the Mode-1 deterministic mate
    sentence is framed in the second person ("you" / "your opponent")
    instead of the detached third-person side name (the #12 probe finding:
    a player up a queen with a forced mate was told "white secures the
    decisive outcome").  Only the subject noun changes, so both Mode-2
    gates must still pass — this is the regression guard against that.

    Comparison is case-insensitive: this fixture uses ``side="White"``
    while ``extract_engine_signal`` emits lowercase in production.
    """

    @staticmethod
    def _hint(player_color: str) -> str:
        return _build_hint(
            uci="h7h8",
            engine_signal=_MATE_ENGINE_SIGNAL,  # side == "White"
            base_explanation="",
            explanation_style="intermediate",
            player_color=player_color,
        )

    def test_player_is_mating_side_says_you_and_passes_gates(self):
        hint = self._hint("white")
        assert "you secure the decisive outcome" in hint.lower()
        assert "white secures" not in hint.lower()
        validate_mode_2_negative(hint)
        validate_mode_2_semantic(hint, _MATE_ENGINE_SIGNAL)

    def test_player_is_mated_side_says_your_opponent_and_passes_gates(self):
        hint = self._hint("black")
        assert "your opponent secures the decisive outcome" in hint.lower()
        validate_mode_2_negative(hint)
        validate_mode_2_semantic(hint, _MATE_ENGINE_SIGNAL)

    def test_unknown_player_color_keeps_third_person_side(self):
        # Default player_color="unknown" must preserve the pre-existing
        # third-person phrasing (backward compatibility for callers that
        # don't supply a colour, and for the gate-safety pins above).
        hint = _build_hint(
            uci="h7h8",
            engine_signal=_MATE_ENGINE_SIGNAL,
            base_explanation="",
            explanation_style="intermediate",
        )
        assert "white secures the decisive outcome" in hint.lower()
        assert "you secure" not in hint.lower()

    @pytest.mark.parametrize("pattern", MATE_CLAIM_PATTERNS)
    def test_perspective_variants_have_no_forbidden_mate_phrase(self, pattern):
        for color in ("white", "black"):
            hint = self._hint(color)
            assert not re.search(pattern, hint, re.IGNORECASE), (
                f"Perspective mate output (player_color={color}) contains the "
                f"forbidden MATE_CLAIM pattern `{pattern}`: {hint!r}"
            )
