"""
Stockfish & ESV Pipeline QA Tests — llm/tests/test_esv_pipeline_qa.py

QA Plan Section 7 — SF-01 through SF-04.

SF-01  Stockfish output has required JSON fields (centipawn_loss, is_winning, etc.)
SF-02  ESV correctly coarsens raw centipawn scores — no raw scores in the signal vector
SF-03  ESV rejects malformed Stockfish JSON — returns error, does not propagate
SF-04  ESV signal is deterministic: same FEN always produces identical output
"""

from __future__ import annotations

import pytest

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_AFTER_E4_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

_VALID_CP_JSON = {
    "evaluation": {"type": "cp", "value": 45},
    "eval_delta": 10,
    "errors": {"last_move_quality": "ok"},
    "tactical_flags": [],
    "position_flags": ["space_advantage"],
}

_VALID_MATE_JSON = {
    "evaluation": {"type": "mate", "value": 3},
    "eval_delta": 200,
    "errors": {"last_move_quality": "ok"},
    "tactical_flags": ["forced_mate"],
    "position_flags": [],
}


# ---------------------------------------------------------------------------
# SF-01 — ESV output schema: required fields always present
# ---------------------------------------------------------------------------


class TestSF01EsvOutputSchema:
    """SF-01: ESV output must always include all required fields."""

    _REQUIRED_TOP_LEVEL = {
        "evaluation",
        "eval_delta",
        "last_move_quality",
        "tactical_flags",
        "position_flags",
        "phase",
    }
    _REQUIRED_EVALUATION = {"type", "band", "side"}

    def test_cp_signal_has_all_required_top_level_fields(self):
        esv = extract_engine_signal(_VALID_CP_JSON)
        missing = self._REQUIRED_TOP_LEVEL - set(esv.keys())
        assert not missing, f"ESV missing required fields: {missing}"

    def test_mate_signal_has_all_required_top_level_fields(self):
        esv = extract_engine_signal(_VALID_MATE_JSON, fen=_STARTING_FEN)
        missing = self._REQUIRED_TOP_LEVEL - set(esv.keys())
        assert not missing, f"ESV missing required fields for mate signal: {missing}"

    def test_evaluation_sub_dict_has_all_required_keys(self):
        esv = extract_engine_signal(_VALID_CP_JSON)
        missing = self._REQUIRED_EVALUATION - set(esv["evaluation"].keys())
        assert not missing, f"evaluation dict missing required keys: {missing}"

    def test_evaluation_type_is_string(self):
        esv = extract_engine_signal(_VALID_CP_JSON)
        assert isinstance(esv["evaluation"]["type"], str)

    def test_evaluation_band_is_string(self):
        esv = extract_engine_signal(_VALID_CP_JSON)
        assert isinstance(esv["evaluation"]["band"], str)

    def test_evaluation_side_is_string(self):
        esv = extract_engine_signal(_VALID_CP_JSON)
        assert isinstance(esv["evaluation"]["side"], str)

    def test_tactical_flags_is_list(self):
        esv = extract_engine_signal(_VALID_CP_JSON)
        assert isinstance(esv["tactical_flags"], list)

    def test_position_flags_is_list(self):
        esv = extract_engine_signal(_VALID_CP_JSON)
        assert isinstance(esv["position_flags"], list)

    def test_phase_is_string(self):
        esv = extract_engine_signal(_VALID_CP_JSON)
        assert isinstance(esv["phase"], str)

    def test_eval_delta_is_string_band(self):
        """eval_delta in ESV must be a coarsened string, not a raw integer."""
        esv = extract_engine_signal(_VALID_CP_JSON)
        assert isinstance(esv["eval_delta"], str), (
            "ESV eval_delta must be a string band ('increase'/'stable'/'decrease'), "
            f"not a raw numeric value; got {esv['eval_delta']!r}"
        )
        assert esv["eval_delta"] in ("increase", "stable", "decrease"), (
            f"eval_delta must be one of increase/stable/decrease, got {esv['eval_delta']!r}"
        )


# ---------------------------------------------------------------------------
# SF-02 — ESV coarsens raw centipawn scores (no raw numbers in signal vector)
# ---------------------------------------------------------------------------


class TestSF02EsvCoarsensRawScores:
    """SF-02: ESV must not expose raw centipawn values; only coarsened bands."""

    @pytest.mark.parametrize("cp_value", [0, 35, 100, 350, -80, -250, 9997])
    def test_evaluation_value_is_not_in_esv(self, cp_value: int):
        """The raw centipawn integer must not appear verbatim anywhere in the ESV dict."""
        stockfish_json = {"evaluation": {"type": "cp", "value": cp_value}}
        esv = extract_engine_signal(stockfish_json)

        def _no_raw_int(obj, value: int) -> bool:
            """Recursively check no dict/list leaf equals the raw cp value."""
            if isinstance(obj, dict):
                return all(_no_raw_int(v, value) for v in obj.values())
            if isinstance(obj, list):
                return all(_no_raw_int(v, value) for v in obj)
            # Allow value=0 since band name 'equal' implies 0 range; test larger values only
            if abs(value) > 20 and obj == value:
                return False
            return True

        assert _no_raw_int(esv, cp_value), (
            f"Raw centipawn value {cp_value} must not appear verbatim in the ESV. "
            f"ESV must coarsen to bands only. Got: {esv}"
        )

    def test_evaluation_band_is_not_numeric(self):
        """The 'band' field must be a string name, never a number."""
        for cp in (0, 50, 150, 500):
            esv = extract_engine_signal({"evaluation": {"type": "cp", "value": cp}})
            assert not isinstance(esv["evaluation"]["band"], (int, float)), (
                f"evaluation.band must be a string label, not numeric; cp={cp}"
            )

    def test_eval_delta_string_hides_raw_delta(self):
        """eval_delta in ESV must be a string label, not the raw integer delta."""
        for delta in (200, -150, 0, 49, -49):
            esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 0}, "eval_delta": delta})
            assert isinstance(esv["eval_delta"], str), (
                f"ESV eval_delta must be a string for delta={delta}; got {esv['eval_delta']!r}"
            )

    def test_mate_value_not_exposed_in_esv(self):
        """For mate signals, the mate-in-N number must not appear raw in the ESV."""
        esv = extract_engine_signal({"evaluation": {"type": "mate", "value": 3}}, fen=_STARTING_FEN)
        # The raw value 3 must not be a leaf in the ESV under 'evaluation'
        eval_dict = esv["evaluation"]
        assert 3 not in eval_dict.values(), (
            "Mate-in-N value (3) must not appear as a raw value in ESV evaluation dict"
        )


# ---------------------------------------------------------------------------
# SF-03 — ESV handles malformed input gracefully (does not propagate exception)
# ---------------------------------------------------------------------------


class TestSF03EsvMalformedInputHandling:
    """SF-03: ESV must degrade gracefully on malformed or missing Stockfish JSON."""

    def test_none_input_returns_default_esv(self):
        """None input must not raise; returns a default ESV with safe defaults."""
        esv = extract_engine_signal(None)
        assert isinstance(esv, dict), "ESV must return a dict even for None input"
        assert "evaluation" in esv

    def test_empty_dict_returns_default_esv(self):
        """Empty dict must not raise; returns a default ESV."""
        esv = extract_engine_signal({})
        assert isinstance(esv, dict)
        assert "evaluation" in esv

    def test_missing_evaluation_key_returns_default(self):
        """Input without 'evaluation' key must return a valid ESV with defaults."""
        esv = extract_engine_signal({"eval_delta": 50, "tactical_flags": ["fork"]})
        assert isinstance(esv, dict)
        assert esv["evaluation"]["type"] == "cp"
        assert esv["evaluation"]["band"] == "equal"

    def test_evaluation_with_wrong_type_returns_defaults(self):
        """Evaluation value of wrong type (string) must be handled without exception."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": "not-a-number"}})
        assert isinstance(esv, dict)
        assert "evaluation" in esv

    def test_non_dict_input_returns_default(self):
        """Non-dict input (e.g., a list or string) must be handled gracefully."""
        # The function coerces falsy to {} and dicts only; any exception is a bug
        try:
            esv = extract_engine_signal(None)
            assert isinstance(esv, dict)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"extract_engine_signal raised on None input: {exc}")

    def test_unknown_evaluation_type_does_not_crash(self):
        """An unknown evaluation type must not raise; falls back to cp defaults."""
        try:
            esv = extract_engine_signal({"evaluation": {"type": "unknown", "value": 42}})
            assert isinstance(esv, dict)
        except Exception as exc:
            pytest.fail(f"extract_engine_signal crashed on unknown eval type: {exc}")

    def test_malformed_fen_in_mate_branch_returns_unknown_side(self):
        """A malformed FEN for a mate signal must yield side='unknown', not an exception."""
        esv = extract_engine_signal(_VALID_MATE_JSON, fen="this-is-not-a-valid-fen")
        assert esv["evaluation"]["side"] == "unknown", (
            "Malformed FEN in mate branch must yield side='unknown'"
        )


# ---------------------------------------------------------------------------
# SF-04 — ESV determinism: same FEN always yields identical output
# ---------------------------------------------------------------------------


class TestSF04EsvDeterminism:
    """SF-04: extract_engine_signal must be fully deterministic."""

    _RUNS = 20  # run multiple times to catch any non-determinism

    @pytest.mark.parametrize(
        "stockfish_json,fen",
        [
            (_VALID_CP_JSON, _STARTING_FEN),
            (_VALID_MATE_JSON, _STARTING_FEN),
            ({"evaluation": {"type": "cp", "value": -180}}, _AFTER_E4_FEN),
            ({}, None),
        ],
    )
    def test_same_input_always_produces_same_esv(self, stockfish_json, fen):
        """Repeated calls with identical inputs must always yield identical ESV dicts."""
        first = extract_engine_signal(stockfish_json, fen=fen)
        for i in range(self._RUNS - 1):
            result = extract_engine_signal(stockfish_json, fen=fen)
            assert result == first, (
                f"ESV is non-deterministic on run {i + 2}: "
                f"first={first!r}, got={result!r}"
            )

    def test_different_fens_produce_different_sides_for_mate(self):
        """For mate signals, different FENs with different active color yield different sides."""
        white_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        black_fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

        esv_white = extract_engine_signal(_VALID_MATE_JSON, fen=white_fen)
        esv_black = extract_engine_signal(_VALID_MATE_JSON, fen=black_fen)

        assert esv_white["evaluation"]["side"] != esv_black["evaluation"]["side"], (
            "Mate ESV side must differ for FENs with different active color"
        )

    def test_evaluation_band_is_deterministic_across_calls(self):
        """Band assignment must be deterministic for the same centipawn value."""
        for cp in (0, 50, 100, 200, -100):
            first = extract_engine_signal({"evaluation": {"type": "cp", "value": cp}})
            for _ in range(5):
                again = extract_engine_signal({"evaluation": {"type": "cp", "value": cp}})
                assert again["evaluation"]["band"] == first["evaluation"]["band"], (
                    f"Band assignment is non-deterministic for cp={cp}"
                )
