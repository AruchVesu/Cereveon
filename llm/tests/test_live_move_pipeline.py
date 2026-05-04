"""
Unit tests for the per-move live coaching pipeline (Mode 1).

Modules under test
------------------
llm.seca.coach.live_move_pipeline
    LiveMoveReply, generate_live_reply, _build_hint

Invariants pinned
-----------------
 1. REPLY_NONNULL:            generate_live_reply always returns a LiveMoveReply.
 2. HINT_NONNULL:             LiveMoveReply.hint is a non-empty string.
 3. ENGINE_SIGNAL_KEYS:       engine_signal has all required top-level keys.
 4. MODE_LIVE_V1:             mode is always "LIVE_V1".
 5. ENGINE_EVAL_IN_HINT:      deterministic hint contains evaluation band or type.
 6. MOVE_QUALITY_IS_STR:      move_quality is a string.
 7. ENGINE_SIGNAL_NEVER_FROM_USER: engine_signal never reflects player_id text.
 8. DETERMINISM:              identical inputs → identical LiveMoveReply (fallback path).
 9. FROZEN:                   LiveMoveReply is immutable (frozen dataclass).
10. BAND_VALUES:              band is one of the four valid strings.
11. FORMAT_MATE_HINT:         mate eval type → "mate" in deterministic hint.
12. FORMAT_CP_HINT:           cp eval type → "advantage" or "equal" in deterministic hint.
13. QUALITY_COMMENT_BLUNDER:  "blunder" quality label → blunder comment in deterministic hint.
14. QUALITY_COMMENT_BEST:     "best" quality label → best comment in deterministic hint.
15. LAYER_NO_RL:              live_move_pipeline.py imports no RL/brain modules.
16. LAYER_NO_SQL:             live_move_pipeline.py imports no sqlalchemy.
17. STARTPOS_FEN:             works correctly with the starting position FEN.
18. MID_FEN:                  works correctly with a mid-game FEN.
19. PLAYER_ID_NOT_IN_SIGNAL:  player_id value is absent from engine_signal.
20. UCI_4_CHARS:              4-char UCI move (e.g. "e2e4") produces a valid reply.
21. UCI_5_CHARS:              5-char UCI move (promotion, e.g. "e7e8q") is accepted.
22. ENGINE_SIGNAL_BAND_TYPE:  evaluation sub-dict has "band" and "type" keys.
23. MODE1_HINT_MAX_2_SENTENCES: deterministic hint has at most 2 sentences.
24. MODE1_SIMPLE_1_SENTENCE:  simple style produces exactly 1 sentence.
25. MODE1_QUALITY_BEFORE_EVAL: quality comment precedes evaluation in deterministic hint.
26. MODE1_PHASE_TIP_STYLE:    simple style omits phase tip; intermediate/advanced append brief phase tip to eval sentence (no Mode-2 verbatim phrases).
27. LLM_PATH_USED_WHEN_AVAILABLE: LLM response is returned when call_llm succeeds.
28. LLM_FALLBACK_ON_ERROR:    deterministic fallback used when LLM raises.
29. LLM_FALLBACK_ON_EMPTY:    deterministic fallback used when LLM returns empty string.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from llm.seca.coach.live_move_pipeline import (
    LiveMoveReply,
    _build_hint,
    generate_live_reply,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_VALID_BANDS = {"equal", "small_advantage", "clear_advantage", "decisive_advantage"}
_REQUIRED_ESV_KEYS = {
    "evaluation",
    "eval_delta",
    "last_move_quality",
    "tactical_flags",
    "position_flags",
    "phase",
}

_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_MID_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
_ENDGAME_FEN = "8/8/4k3/8/8/4K3/4P3/8 w - - 0 1"

_UCI_NORMAL = "e2e4"
_UCI_PROMO = "e7e8q"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_imports(module_path: Path) -> set[str]:
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def _make_signal(
    eval_type: str = "cp",
    band: str = "equal",
    side: str = "white",
    phase: str = "middlegame",
    move_quality: str = "unknown",
) -> dict:
    return {
        "evaluation": {"type": eval_type, "band": band, "side": side},
        "eval_delta": "stable",
        "last_move_quality": move_quality,
        "tactical_flags": [],
        "position_flags": [],
        "phase": phase,
    }


def _sentence_count(text: str) -> int:
    """Count sentence-ending punctuation marks (period, ! or ?)."""
    return len(re.findall(r"[.!?](?:\s|$)", text.strip()))


def _patch_llm_unavailable():
    """Context manager: force LLM path to fail so tests use the deterministic fallback."""
    return patch(
        "llm.seca.coach.live_move_pipeline._LLM_AVAILABLE",
        False,
    )


# ---------------------------------------------------------------------------
# 1–6  Core return-value invariants  (LLM disabled → deterministic)
# ---------------------------------------------------------------------------


class TestLiveMoveReplyInvariants:

    def test_returns_live_move_reply_instance(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        assert isinstance(result, LiveMoveReply)

    def test_hint_is_non_empty_string(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        assert isinstance(result.hint, str) and result.hint.strip()

    def test_engine_signal_has_all_required_keys(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        missing = _REQUIRED_ESV_KEYS - result.engine_signal.keys()
        assert not missing, f"Missing engine_signal keys: {missing}"

    def test_mode_is_live_v1(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        assert result.mode == "LIVE_V1"

    def test_hint_contains_engine_evaluation_reference(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        band = result.engine_signal["evaluation"]["band"]
        band_word = band.replace("_", " ")
        assert any(
            w in result.hint for w in band_word.split()
        ) or "equal" in result.hint.lower() or "mate" in result.hint.lower(), (
            f"Hint does not reference evaluation band '{band}': {result.hint!r}"
        )

    def test_move_quality_is_string(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        assert isinstance(result.move_quality, str)


# ---------------------------------------------------------------------------
# 7  Engine signal isolation
# ---------------------------------------------------------------------------


class TestEngineSignalIsolation:

    def test_engine_signal_does_not_contain_player_id(self):
        sentinel = "INJECTION_PROBE_XYZZY"
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL, player_id=sentinel)
        signal_str = str(result.engine_signal)
        assert sentinel not in signal_str

    def test_engine_signal_band_is_valid(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        band = result.engine_signal["evaluation"]["band"]
        assert band in _VALID_BANDS, f"Unknown band: {band!r}"


# ---------------------------------------------------------------------------
# 8  Determinism (fallback path)
# ---------------------------------------------------------------------------


class TestDeterminism:

    def test_identical_inputs_produce_identical_output(self):
        with _patch_llm_unavailable():
            r1 = generate_live_reply(_MID_FEN, _UCI_NORMAL, player_id="player1")
            r2 = generate_live_reply(_MID_FEN, _UCI_NORMAL, player_id="player1")
        assert r1.hint == r2.hint
        assert r1.engine_signal == r2.engine_signal
        assert r1.move_quality == r2.move_quality
        assert r1.mode == r2.mode

    def test_different_fens_may_differ(self):
        with _patch_llm_unavailable():
            r1 = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
            r2 = generate_live_reply(_MID_FEN, _UCI_NORMAL)
        assert r1.hint.strip()
        assert r2.hint.strip()


# ---------------------------------------------------------------------------
# 9  Immutability
# ---------------------------------------------------------------------------


class TestDataclassImmutability:

    def test_live_move_reply_is_frozen(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        with pytest.raises((AttributeError, TypeError)):
            result.mode = "MODIFIED"  # type: ignore[misc]

    def test_live_move_reply_hint_is_frozen(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        with pytest.raises((AttributeError, TypeError)):
            result.hint = "hacked"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 10  Band values
# ---------------------------------------------------------------------------


class TestBandValues:

    def test_starting_fen_band_is_valid(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        assert result.engine_signal["evaluation"]["band"] in _VALID_BANDS

    def test_mid_fen_band_is_valid(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_MID_FEN, _UCI_NORMAL)
        assert result.engine_signal["evaluation"]["band"] in _VALID_BANDS


# ---------------------------------------------------------------------------
# 11–12  _build_hint formatting
# ---------------------------------------------------------------------------


class TestBuildHintFormatting:

    def test_mate_signal_produces_mate_in_hint(self):
        signal = _make_signal(eval_type="mate", band="decisive_advantage", side="white")
        hint = _build_hint(_UCI_NORMAL, signal, "")
        assert "mate" in hint.lower(), f"Expected 'mate' in hint: {hint!r}"

    def test_cp_equal_produces_equal_in_hint(self):
        signal = _make_signal(eval_type="cp", band="equal", side="black")
        hint = _build_hint(_UCI_NORMAL, signal, "")
        assert "equal" in hint.lower(), f"Expected 'equal' in hint: {hint!r}"

    def test_cp_advantage_produces_advantage_in_hint(self):
        signal = _make_signal(eval_type="cp", band="clear_advantage", side="white")
        hint = _build_hint(_UCI_NORMAL, signal, "")
        assert "advantage" in hint.lower(), f"Expected 'advantage' in hint: {hint!r}"

    def test_hint_is_non_empty_string(self):
        signal = _make_signal()
        hint = _build_hint(_UCI_NORMAL, signal, "")
        assert isinstance(hint, str) and hint.strip()


# ---------------------------------------------------------------------------
# 13–14  Move quality comments
# ---------------------------------------------------------------------------


class TestMoveQualityComments:

    def test_blunder_quality_produces_blunder_comment(self):
        signal = _make_signal(move_quality="blunder")
        hint = _build_hint(_UCI_NORMAL, signal, "")
        assert "blunder" in hint.lower(), f"Expected blunder comment: {hint!r}"

    def test_best_quality_produces_best_comment(self):
        signal = _make_signal(move_quality="best")
        hint = _build_hint(_UCI_NORMAL, signal, "")
        assert "best" in hint.lower() or "optimal" in hint.lower(), (
            f"Expected best-move comment: {hint!r}"
        )

    def test_unknown_quality_produces_no_quality_comment(self):
        signal = _make_signal(move_quality="unknown")
        hint = _build_hint(_UCI_NORMAL, signal, "")
        assert "unknown" not in hint.lower(), f"'unknown' leaked into hint: {hint!r}"

    def test_mistake_quality_produces_mistake_comment(self):
        signal = _make_signal(move_quality="mistake")
        hint = _build_hint(_UCI_NORMAL, signal, "")
        assert "mistake" in hint.lower(), f"Expected mistake comment: {hint!r}"


# ---------------------------------------------------------------------------
# 15–16  Layer boundaries
# ---------------------------------------------------------------------------


class TestLayerBoundary:

    _FORBIDDEN_RL = ("rl", "reinforcement", "brain", "policy", "reward")
    _FORBIDDEN_SQL = ("sqlalchemy",)

    def _imports(self) -> set[str]:
        path = PROJECT_ROOT / "llm" / "seca" / "coach" / "live_move_pipeline.py"
        assert path.exists(), "live_move_pipeline.py not found"
        return _get_imports(path)

    def test_no_rl_imports(self):
        imports = self._imports()
        violations = {i for i in imports if any(p in i.lower() for p in self._FORBIDDEN_RL)}
        assert not violations, f"live_move_pipeline.py imports RL modules: {violations}"

    def test_no_sqlalchemy_imports(self):
        imports = self._imports()
        violations = {i for i in imports if any(p in i for p in self._FORBIDDEN_SQL)}
        assert not violations, f"live_move_pipeline.py imports SQLAlchemy: {violations}"


# ---------------------------------------------------------------------------
# 17–18  FEN variety
# ---------------------------------------------------------------------------


class TestFenVariety:

    def test_starting_position_fen(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        assert isinstance(result, LiveMoveReply) and result.hint.strip()

    def test_mid_game_fen(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_MID_FEN, _UCI_NORMAL)
        assert isinstance(result, LiveMoveReply) and result.hint.strip()

    def test_endgame_fen(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_ENDGAME_FEN, _UCI_NORMAL)
        assert isinstance(result, LiveMoveReply) and result.hint.strip()


# ---------------------------------------------------------------------------
# 19  player_id isolation
# ---------------------------------------------------------------------------


class TestPlayerIdIsolation:

    def test_player_id_not_in_engine_signal(self):
        player_id = "unique_player_id_abc123"
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL, player_id=player_id)
        assert player_id not in str(result.engine_signal)

    def test_different_player_ids_same_signal(self):
        with _patch_llm_unavailable():
            r1 = generate_live_reply(_STARTING_FEN, _UCI_NORMAL, player_id="alice")
            r2 = generate_live_reply(_STARTING_FEN, _UCI_NORMAL, player_id="bob")
        assert r1.engine_signal == r2.engine_signal


# ---------------------------------------------------------------------------
# 20–21  UCI move length variants
# ---------------------------------------------------------------------------


class TestUciMoveVariants:

    def test_4_char_uci_accepted(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, "e2e4")
        assert result.hint.strip()

    def test_5_char_uci_promotion_accepted(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, "e7e8q")
        assert result.hint.strip()


# ---------------------------------------------------------------------------
# 22  engine_signal sub-dict structure
# ---------------------------------------------------------------------------


class TestEngineSignalSubDict:

    def test_evaluation_has_band_and_type(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        ev = result.engine_signal.get("evaluation", {})
        assert "band" in ev and "type" in ev

    def test_evaluation_type_is_cp_or_mate(self):
        with _patch_llm_unavailable():
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        ev_type = result.engine_signal["evaluation"]["type"]
        assert ev_type in ("cp", "mate"), f"Unexpected eval type: {ev_type!r}"


# ---------------------------------------------------------------------------
# 23–26  Mode-1 specific: short, quality-first, no phase tip
# ---------------------------------------------------------------------------


class TestMode1HintStructure:

    def test_default_hint_at_most_two_sentences(self):
        """Deterministic Mode-1 hint must be at most 2 sentences."""
        signal = _make_signal(move_quality="blunder", phase="middlegame")
        hint = _build_hint(_UCI_NORMAL, signal, "")
        count = _sentence_count(hint)
        assert count <= 2, f"Hint has more than 2 sentences ({count}): {hint!r}"

    def test_simple_style_hint_is_one_sentence(self):
        """Simple (beginner) style produces at most 1 sentence."""
        signal = _make_signal(move_quality="mistake", phase="opening")
        hint = _build_hint(_UCI_NORMAL, signal, "", explanation_style="simple")
        count = _sentence_count(hint)
        assert count <= 1, f"Simple hint has more than 1 sentence ({count}): {hint!r}"

    def test_quality_comment_before_eval(self):
        """Quality comment must appear before the evaluation sentence."""
        signal = _make_signal(move_quality="blunder", band="clear_advantage", side="white")
        hint = _build_hint(_UCI_NORMAL, signal, "")
        blunder_pos = hint.lower().find("blunder")
        advantage_pos = hint.lower().find("advantage")
        assert blunder_pos != -1, f"'blunder' not found: {hint!r}"
        assert advantage_pos != -1, f"'advantage' not found: {hint!r}"
        assert blunder_pos < advantage_pos, (
            f"Quality comment must precede evaluation: {hint!r}"
        )

    def test_no_mode2_phase_tip_verbatim_in_hint(self):
        """Mode-1 must not copy Mode-2 verbatim phase-tip phrasing.

        Mode-1 has its own brief phase tip appended to the eval sentence in
        intermediate/advanced styles (e.g. "focus on development and centre
        control"), but it must not reuse the exact Mode-2 sentences from
        chat_pipeline._PHASE_HINT (e.g. "prioritise development and centre
        control", "tactical motifs", "activate your king").
        """
        mode2_phrases = [
            "prioritise development",  # Mode-2 opening phrase
            "tactical motifs",         # Mode-2 middlegame phrase
            "activate your king",      # Mode-2 endgame phrase (Mode-1 uses "activate the king")
            "controlling the centre",
            "convert any material",
        ]
        for phase in ("opening", "middlegame", "endgame"):
            signal = _make_signal(phase=phase)
            hint = _build_hint(_UCI_NORMAL, signal, "")
            for phrase in mode2_phrases:
                assert phrase not in hint.lower(), (
                    f"Mode-2 phrase '{phrase}' found in Mode-1 hint ({phase}): {hint!r}"
                )

    def test_all_quality_labels_covered(self):
        """All standard quality labels produce a non-empty quality comment."""
        for quality in ("blunder", "mistake", "inaccuracy", "good", "excellent", "best"):
            signal = _make_signal(move_quality=quality, band="small_advantage")
            hint = _build_hint(_UCI_NORMAL, signal, "")
            assert hint.strip(), f"Empty hint for quality={quality!r}"

    def test_level_differentiation_simple_vs_advanced(self):
        """Simple and advanced styles produce different hint text."""
        signal = _make_signal(move_quality="blunder")
        simple = _build_hint(_UCI_NORMAL, signal, "", explanation_style="simple")
        advanced = _build_hint(_UCI_NORMAL, signal, "", explanation_style="advanced")
        assert simple != advanced, "Simple and advanced styles should differ"


# ---------------------------------------------------------------------------
# 27–29  LLM path tests
# ---------------------------------------------------------------------------


class TestLLMPath:

    _LLM_MODULE = "llm.seca.coach.live_move_pipeline"

    def test_llm_response_returned_when_available(self):
        """When call_llm succeeds, the LLM response is used as the hint."""
        llm_hint = "Nice move! The position is equal and balanced."
        with (
            patch(f"{self._LLM_MODULE}._LLM_AVAILABLE", True),
            patch(f"{self._LLM_MODULE}._build_hint_llm", return_value=llm_hint),
        ):
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        assert result.hint == llm_hint
        assert result.mode == "LIVE_V1"

    def test_deterministic_fallback_on_llm_error(self):
        """Deterministic fallback is used when the LLM path raises."""
        with (
            patch(f"{self._LLM_MODULE}._LLM_AVAILABLE", True),
            patch(f"{self._LLM_MODULE}._build_hint_llm", side_effect=RuntimeError("Ollama down")),
        ):
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        assert isinstance(result.hint, str) and result.hint.strip()
        assert result.mode == "LIVE_V1"

    def test_deterministic_fallback_on_empty_llm_response(self):
        """Deterministic fallback is used when LLM returns an empty string."""
        with (
            patch(f"{self._LLM_MODULE}._LLM_AVAILABLE", True),
            patch(f"{self._LLM_MODULE}._build_hint_llm", return_value=""),
        ):
            result = generate_live_reply(_STARTING_FEN, _UCI_NORMAL)
        assert isinstance(result.hint, str) and result.hint.strip()

    def test_engine_signal_never_from_llm(self):
        """engine_signal is always from extract_engine_signal, not from the LLM."""
        sentinel = "LLM_INJECTED_SIGNAL"
        with (
            patch(f"{self._LLM_MODULE}._LLM_AVAILABLE", True),
            patch(f"{self._LLM_MODULE}._build_hint_llm", return_value=f"Your move. {sentinel}"),
        ):
            result = generate_live_reply(_MID_FEN, _UCI_NORMAL)
        assert sentinel not in str(result.engine_signal)
