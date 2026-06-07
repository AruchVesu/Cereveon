"""
Regression tests for retiring general strategic vocabulary from the
Mode-2 SEMANTIC validator (2026-06-06).

Context
-------
``validate_mode_2_semantic`` rejected, as substring matches:
  - any of TACTICAL_NOUN_WORDS when ``tactical_flags == []``
  - any of EQUAL_ADVANTAGE_WORDS when ``evaluation.band == "equal"``

The lists conflated *concrete* claims that contradict engine truth with
*general* coaching vocabulary:
  - "attack" / "threat" are not tactical MOTIFS (unlike fork / pin /
    sacrifice) — "build an attack", "threats against the king" are normal
    strategic talk.
  - "initiative" / "pressure" are not direct advantage CLAIMS (unlike
    "slight advantage" / "better" / "winning") — "both sides apply
    pressure", "fight for the initiative" are equal-acknowledging.

On a quiet, equal position these substring vetoes fired on almost every
Mode-2 LLM reply — most visibly king-safety questions ("why is this bad
for my king?"), whose natural answer REQUIRES attack/threat/pressure —
so every retry failed the semantic gate and the pipeline fell through to
the templated deterministic fallback (the "nothing changed" report,
2026-06-06).

The retirement keeps the guarantees that actually matter:
  - inventing a concrete motif (fork / pin / sacrifice) with no engine
    flag is still rejected;
  - claiming "slight advantage" / "better" / "winning" on an equal board
    is still rejected.

Same false-positive-retirement shape as ``\\bshould\\b`` (PR #170) and
``\\bplan\\b`` (2026-06-04).
"""

from __future__ import annotations

import pytest

from llm.rag.validators._rules import EQUAL_ADVANTAGE_WORDS, TACTICAL_NOUN_WORDS
from llm.rag.validators.mode_2_semantic import Mode2Violation, validate_mode_2_semantic
from llm.seca.coach._mode_2_validators import validate_mode_2_or_raise


# ---------------------------------------------------------------------------
# Pin 1 — the retired words are gone; the load-bearing ones remain
# ---------------------------------------------------------------------------


class TestRetiredFromSemanticLists:
    @pytest.mark.parametrize("word", ["attack", "threat"])
    def test_general_word_not_in_tactical_list(self, word):
        assert word not in TACTICAL_NOUN_WORDS, (
            f"'{word}' was re-added to TACTICAL_NOUN_WORDS.  It is a general "
            f"strategic word, not a concrete tactical motif, and re-adding it "
            f"re-breaks king-safety / strategy answers on quiet positions.  "
            f"Keep only the motifs (fork/pin/sacrifice)."
        )

    @pytest.mark.parametrize("word", ["initiative", "pressure"])
    def test_general_word_not_in_equal_list(self, word):
        assert word not in EQUAL_ADVANTAGE_WORDS, (
            f"'{word}' was re-added to EQUAL_ADVANTAGE_WORDS.  It is general "
            f"strategic vocab, not a direct advantage claim; re-adding it "
            f"re-breaks equal-position coaching.  Keep only the direct "
            f"who-stands-better claims (slight advantage/better/winning)."
        )

    def test_motifs_and_advantage_claims_retained(self):
        for motif in ("fork", "pin", "sacrifice"):
            assert motif in TACTICAL_NOUN_WORDS, f"lost the '{motif}' motif guard"
        # "better" retired from EQUAL_ADVANTAGE_WORDS 2026-06-07 (too
        # common a comparative to distinguish from the advantage claim).
        for claim in ("slight advantage", "winning"):
            assert claim in EQUAL_ADVANTAGE_WORDS, f"lost the '{claim}' advantage guard"


# ---------------------------------------------------------------------------
# Pin 2 — retired words now PASS the semantic gate
# ---------------------------------------------------------------------------


class TestRetiredWordsPassSemantic:
    @pytest.mark.parametrize(
        "text",
        [
            "The opponent can build an attack against your exposed king.",
            "There are threats against the weakened squares near your king.",
        ],
    )
    def test_general_tactical_words_pass_with_empty_flags(self, text):
        # tactical_flags == [] used to reject these; now it must not.
        validate_mode_2_semantic(
            text, {"evaluation": {"band": "small_advantage"}, "tactical_flags": []}
        )

    @pytest.mark.parametrize(
        "text",
        [
            "Both sides apply pressure in the centre.",
            "Each side fights for the initiative.",
        ],
    )
    def test_general_advantage_words_pass_on_equal_band(self, text):
        # band == "equal" used to reject these; now it must not.
        validate_mode_2_semantic(
            text, {"evaluation": {"band": "equal"}, "tactical_flags": ["x"]}
        )


# ---------------------------------------------------------------------------
# Pin 3 — the load-bearing guards STILL reject
# ---------------------------------------------------------------------------


class TestKeptGuardsStillReject:
    @pytest.mark.parametrize("motif", ["fork", "pin", "sacrifice"])
    def test_invented_motif_still_rejected(self, motif):
        with pytest.raises(Mode2Violation, match="Invented tactic without flag"):
            validate_mode_2_semantic(
                f"there is a {motif} that decides it", {"tactical_flags": []}
            )

    @pytest.mark.parametrize("claim", ["slight advantage", "winning"])
    def test_advantage_claim_on_equal_still_rejected(self, claim):
        with pytest.raises(Mode2Violation, match="Equal position described as advantage"):
            validate_mode_2_semantic(
                f"white is clearly {claim} here", {"evaluation": {"band": "equal"}}
            )


# ---------------------------------------------------------------------------
# Pin 3b — word-boundary matching (2026-06-07): semantic checks switched
# from raw substring to ``\b...\b`` regex, so a tactic like "pin" must not
# fire inside "develoPINg" / "stepPINg".  Also pins that the freshly
# retired surfaces ("better" on equal-band, "wants to" speculative) no
# longer raise on their own surfaces.
# ---------------------------------------------------------------------------


class TestWordBoundaryAndRetiredSurfaces:
    def test_pin_does_not_match_inside_other_words(self):
        """The substring "pin" lives inside "developing"/"stepping"/
        "keeping"; word-boundary matching must NOT treat that as the
        invented-tactic motif.  Empty tactical_flags would have raised
        "Invented tactic without flag: 'pin'" under the old substring
        match, dropping ordinary coaching prose to the templated
        fallback."""
        validate_mode_2_semantic(
            "we are developing and stepping carefully", {"tactical_flags": []}
        )

    def test_better_passes_on_equal_band(self):
        """"better" was retired from EQUAL_ADVANTAGE_WORDS 2026-06-07;
        its surface ("a better square", "better to castle first") must
        no longer raise even on an equal band."""
        validate_mode_2_semantic(
            "a better square for the knight keeps things balanced",
            {"evaluation": {"band": "equal"}},
        )

    def test_wants_to_passes_speculative_surface(self):
        """"wants to" was retired from SPECULATIVE_SEMANTIC 2026-06-07
        (caught ordinary coaching "your opponent wants to open the
        centre" far more than the engine-voice sense); its surface must
        no longer raise."""
        validate_mode_2_semantic(
            "your opponent wants to open the centre", {"evaluation": {}}
        )


# ---------------------------------------------------------------------------
# Pin 4 — realistic king-safety answers now clear the FULL boundary stack
# ---------------------------------------------------------------------------


class TestKingSafetyAnswersPassFullStack:
    """The reported scenario: opening, BLACK: EQUAL, quiet (no flags),
    question 'why is this bad for my king?'.  These prompt-compliant
    answers (no notation, no specific move) must pass firewall + negative
    + structure + semantic, where before they died on the semantic gate."""

    _ESV = {
        "evaluation": {"band": "equal", "type": "cp", "side": "black"},
        "tactical_flags": [],
        "phase": "opening",
    }

    @pytest.mark.parametrize(
        "answer",
        [
            "Your king is a little exposed because the pawns in front of it "
            "have moved, leaving weak squares nearby. The opponent can target "
            "those squares and build an attack. Focus on finishing development "
            "and tucking your king to safety.",
            "Advancing that pawn loosens the shelter around your king, and the "
            "opponent can generate threats against the weakened squares. "
            "Prioritise king safety and complete your development first.",
            "By weakening the squares around your king you give the other side "
            "something to aim at, and the pressure on your position grows. Aim "
            "to castle into safety and keep your structure intact.",
        ],
    )
    def test_king_safety_answer_passes(self, answer):
        validate_mode_2_or_raise(answer, self._ESV)
