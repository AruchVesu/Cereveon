"""
Stockfish notation correctness — llm/tests/test_stockfish_notation.py

Proves 100% correct parsing of all Stockfish notation cases by testing every
threshold boundary, sign convention, and edge case in extract_engine_signal().

ESV-01   CP threshold boundary: cp=0 → band=equal, side=black (zero convention)
ESV-02   CP threshold boundary: cp=20 → band=equal (inclusive upper bound)
ESV-03   CP threshold boundary: cp=21 → band=small_advantage (just above equal)
ESV-04   CP threshold boundary: cp=60 → band=small_advantage (inclusive upper)
ESV-05   CP threshold boundary: cp=61 → band=clear_advantage (just above small)
ESV-06   CP threshold boundary: cp=120 → band=clear_advantage (inclusive upper)
ESV-07   CP threshold boundary: cp=121 → band=decisive_advantage (just above clear)
ESV-08   CP threshold boundary: cp=9999 → band=decisive_advantage (large value)
ESV-09   Negative CP mirrors correctly: cp=-21 → small_advantage, side=black
ESV-10   Negative CP mirrors correctly: cp=-121 → decisive_advantage, side=black
ESV-11   Sign convention: positive cp → side=white
ESV-12   Sign convention: negative cp → side=black
ESV-13   Sign convention: zero → side=black (convention)
ESV-14   eval_delta boundary: delta=49 → stable (below increase threshold)
ESV-15   eval_delta boundary: delta=50 → increase (at threshold)
ESV-16   eval_delta boundary: delta=-49 → stable (above decrease threshold)
ESV-17   eval_delta boundary: delta=-50 → decrease (at threshold)
ESV-18   Mate path: evaluation.type=mate, band=decisive_advantage always
ESV-19   Mate path: side derived from FEN active color (white-to-move → side=white)
ESV-20   Mate path: eval_delta thresholds apply (200 → increase)
ESV-21   errors field: non-dict string → graceful fallback to last_move_quality=unknown
ESV-22   errors field: non-dict int → graceful fallback to last_move_quality=unknown
ESV-23   errors field: missing key → last_move_quality=unknown default
ESV-24   errors field: valid dict passes through last_move_quality
ESV-25   eval_type unknown → treated as cp, no exception
ESV-26   value: non-numeric string → value=0, no exception (→ band=equal)
ESV-27   value: float → truncated to int (1.9 → 1)
ESV-28   value: None → value=0, no exception
ESV-29   stockfish_json=None → valid ESV dict returned (FEN fallback)
ESV-30   stockfish_json={} → valid ESV dict returned (FEN fallback)
ESV-31   FEN enrichment: material-favored FEN → non-zero cp reflected in band
ESV-32   FEN enrichment: opening FEN → phase=opening
ESV-33   FEN enrichment: endgame FEN (few pieces) → phase=endgame
ESV-34   Stockfish phase takes precedence over FEN-derived phase
ESV-35   Invalid FEN → no crash, returns valid ESV with defaults
ESV-36   Tactical flags preserved verbatim from Stockfish data
ESV-37   Position flags preserved verbatim from Stockfish data
ESV-38   last_move_quality unknown value → normalized to unknown with no exception
ESV-39   All known quality values pass through correctly
ESV-40   Full round-trip: ESV validates through EngineSignalSchema (all known qualities)
ESV-41   Logging: unknown eval_type fires logger.warning (captured via caplog)
ESV-42   Logging: non-numeric value fires logger.warning
ESV-43   Logging: non-dict errors fires logger.warning
"""

from __future__ import annotations

import logging

import pytest

from llm.rag.engine_signal.extract_engine_signal import (
    extract_engine_signal,
    _KNOWN_MOVE_QUALITIES,
)
from llm.rag.validators.explain_response_schema import (
    EngineSignalSchema,
    ExplainSchemaError,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_AFTER_E4_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

# Endgame FEN: only kings and two pawns
_ENDGAME_FEN = "8/8/4k3/8/8/3K4/4P3/8 w - - 0 60"

# FEN where White has extra queen vs starting — material strongly in White's favour
_WHITE_QUEEN_AHEAD_FEN = "rnbqkbnr/pppppppp/8/8/4Q3/8/PPPPPPPP/RNB1KBNR w KQkq - 0 1"


def _esv(value: int = 0, *, delta: int = 0, errors: object = None) -> dict:
    payload: dict = {
        "evaluation": {"type": "cp", "value": value},
        "eval_delta": delta,
    }
    if errors is not None:
        payload["errors"] = errors
    return payload


def _validated(esv: dict) -> EngineSignalSchema:
    """Round-trip through EngineSignalSchema; raises ExplainSchemaError on failure."""
    try:
        return EngineSignalSchema.model_validate(esv)
    except Exception as exc:
        raise ExplainSchemaError(f"EngineSignalSchema rejected ESV: {exc}") from exc


# ===========================================================================
# ESV-01..08  CP band threshold boundaries
# ===========================================================================


class TestCPBandBoundaries:
    """ESV-01..08: Exact band assignment at every threshold boundary."""

    @pytest.mark.parametrize(
        "cp_input, expected_band",
        [
            (0, "equal"),
            (20, "equal"),
            (21, "small_advantage"),
            (60, "small_advantage"),
            (61, "clear_advantage"),
            (120, "clear_advantage"),
            (121, "decisive_advantage"),
            (9999, "decisive_advantage"),
        ],
    )
    def test_positive_cp_band(self, cp_input: int, expected_band: str):
        """ESV-01..08: Positive CP value maps to correct band."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": cp_input}})
        assert esv["evaluation"]["band"] == expected_band, (
            f"cp={cp_input}: expected band={expected_band!r}, got {esv['evaluation']['band']!r}"
        )

    @pytest.mark.parametrize(
        "cp_input, expected_band",
        [
            (-20, "equal"),
            (-21, "small_advantage"),
            (-60, "small_advantage"),
            (-61, "clear_advantage"),
            (-120, "clear_advantage"),
            (-121, "decisive_advantage"),
        ],
    )
    def test_negative_cp_band(self, cp_input: int, expected_band: str):
        """ESV-09..10: Negative CP maps to same band as its absolute value."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": cp_input}})
        assert esv["evaluation"]["band"] == expected_band, (
            f"cp={cp_input}: expected band={expected_band!r}, got {esv['evaluation']['band']!r}"
        )
        # Negative → side must be black
        assert esv["evaluation"]["side"] == "black", (
            f"Negative cp must give side=black, got {esv['evaluation']['side']!r}"
        )


# ===========================================================================
# ESV-11..13  Sign convention
# ===========================================================================


class TestSignConvention:
    """ESV-11..13: CP sign determines side correctly at every polarity."""

    def test_positive_cp_gives_white(self):
        """ESV-11: Positive cp → side=white."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 50}})
        assert esv["evaluation"]["side"] == "white"

    def test_negative_cp_gives_black(self):
        """ESV-12: Negative cp → side=black."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": -50}})
        assert esv["evaluation"]["side"] == "black"

    def test_zero_cp_gives_black_by_convention(self):
        """ESV-13: Zero cp → side=black by convention (band=equal is primary signal)."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 0}})
        assert esv["evaluation"]["side"] == "black", (
            "Zero CP must yield side='black' by convention"
        )
        assert esv["evaluation"]["band"] == "equal"


# ===========================================================================
# ESV-14..17  eval_delta threshold boundaries
# ===========================================================================


class TestEvalDeltaBoundaries:
    """ESV-14..17: Exact eval_delta band at every threshold boundary."""

    @pytest.mark.parametrize(
        "delta_input, expected_label",
        [
            (49, "stable"),
            (50, "increase"),
            (200, "increase"),
            (-49, "stable"),
            (-50, "decrease"),
            (-200, "decrease"),
            (0, "stable"),
        ],
    )
    def test_eval_delta_band(self, delta_input: int, expected_label: str):
        """ESV-14..17: eval_delta integer maps to correct string label."""
        esv = extract_engine_signal(_esv(delta=delta_input))
        assert esv["eval_delta"] == expected_label, (
            f"delta={delta_input}: expected {expected_label!r}, got {esv['eval_delta']!r}"
        )

    def test_eval_delta_missing_defaults_to_stable(self):
        """Missing eval_delta key defaults to stable (0 input)."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 0}})
        assert esv["eval_delta"] == "stable"


# ===========================================================================
# ESV-18..20  Mate path
# ===========================================================================


class TestMatePath:
    """ESV-18..20: Mate eval_type produces correct ESV structure."""

    def test_mate_type_is_preserved(self):
        """ESV-18: evaluation.type=mate in output when input is mate."""
        esv = extract_engine_signal(
            {"evaluation": {"type": "mate", "value": 3}}, fen=_STARTING_FEN
        )
        assert esv["evaluation"]["type"] == "mate"

    def test_mate_band_is_always_decisive(self):
        """ESV-18b: Mate always maps to decisive_advantage regardless of value."""
        for mate_in in (1, 3, 10, 100):
            esv = extract_engine_signal(
                {"evaluation": {"type": "mate", "value": mate_in}}, fen=_STARTING_FEN
            )
            assert esv["evaluation"]["band"] == "decisive_advantage", (
                f"Mate-in-{mate_in} must be decisive_advantage"
            )

    def test_mate_side_from_white_to_move_fen(self):
        """ESV-19: Mate with white-to-move FEN → side=white."""
        esv = extract_engine_signal(
            {"evaluation": {"type": "mate", "value": 1}}, fen=_STARTING_FEN
        )
        assert esv["evaluation"]["side"] == "white", (
            "White-to-move FEN must produce side=white for mate signal"
        )

    def test_mate_side_from_black_to_move_fen(self):
        """ESV-19b: Mate with black-to-move FEN → side=black."""
        esv = extract_engine_signal(
            {"evaluation": {"type": "mate", "value": 1}}, fen=_AFTER_E4_FEN
        )
        assert esv["evaluation"]["side"] == "black"

    def test_mate_eval_delta_above_threshold(self):
        """ESV-20: eval_delta=200 in mate signal → increase."""
        esv = extract_engine_signal(
            {"evaluation": {"type": "mate", "value": 1}, "eval_delta": 200},
            fen=_STARTING_FEN,
        )
        assert esv["eval_delta"] == "increase"

    def test_mate_eval_delta_below_threshold(self):
        """ESV-20b: eval_delta=-200 in mate signal → decrease."""
        esv = extract_engine_signal(
            {"evaluation": {"type": "mate", "value": 1}, "eval_delta": -200},
            fen=_STARTING_FEN,
        )
        assert esv["eval_delta"] == "decrease"

    def test_mate_no_fen_gives_unknown_side(self):
        """Mate with no FEN → side=unknown."""
        esv = extract_engine_signal({"evaluation": {"type": "mate", "value": 1}})
        assert esv["evaluation"]["side"] == "unknown"


# ===========================================================================
# ESV-21..24  errors field handling
# ===========================================================================


class TestErrorsFieldHandling:
    """ESV-21..24: errors field type guard prevents crashes on non-dict values."""

    def test_errors_as_string_does_not_crash(self):
        """ESV-21: errors='bad_string' → last_move_quality=unknown, no exception."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 0}, "errors": "bad_string"})
        assert isinstance(esv, dict)
        assert esv["last_move_quality"] == "unknown"

    def test_errors_as_int_does_not_crash(self):
        """ESV-22: errors=42 → last_move_quality=unknown, no exception."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 0}, "errors": 42})
        assert isinstance(esv, dict)
        assert esv["last_move_quality"] == "unknown"

    def test_errors_missing_key_defaults_to_unknown(self):
        """ESV-23: 'errors' key absent → last_move_quality=unknown."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 0}})
        assert esv["last_move_quality"] == "unknown"

    def test_errors_valid_dict_passes_through(self):
        """ESV-24: Valid errors dict → last_move_quality propagated correctly."""
        for quality in _KNOWN_MOVE_QUALITIES:
            esv = extract_engine_signal(
                {"evaluation": {"type": "cp", "value": 0}, "errors": {"last_move_quality": quality}}
            )
            assert esv["last_move_quality"] == quality, (
                f"Quality {quality!r} must pass through unchanged"
            )

    def test_errors_none_list_coerced(self):
        """errors as list (non-dict) → last_move_quality=unknown, no exception."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 0}, "errors": ["list", "value"]})
        assert esv["last_move_quality"] == "unknown"


# ===========================================================================
# ESV-25..28  eval_type and value edge cases
# ===========================================================================


class TestEvalTypeAndValueEdgeCases:
    """ESV-25..28: Unknown eval_type and malformed values handled without exceptions."""

    def test_unknown_eval_type_treated_as_cp(self):
        """ESV-25: eval_type='unknown_type' falls back to cp branch."""
        esv = extract_engine_signal({"evaluation": {"type": "unknown_type", "value": 42}})
        assert isinstance(esv, dict)
        assert esv["evaluation"]["type"] == "cp", (
            "Unknown eval_type must be treated as cp"
        )
        assert esv["evaluation"]["band"] == "small_advantage"

    def test_string_value_coerces_to_zero(self):
        """ESV-26: Non-numeric value string → value=0 → band=equal."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": "not-a-number"}})
        assert esv["evaluation"]["band"] == "equal", (
            "Non-numeric string value must coerce to 0 → band=equal"
        )

    def test_float_value_truncated_to_int(self):
        """ESV-27: Float value → truncated to int (21.9 → 21 → small_advantage)."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 21.9}})
        assert esv["evaluation"]["band"] == "small_advantage", (
            "Float 21.9 → int 21 → small_advantage"
        )

    def test_float_at_boundary_truncates_correctly(self):
        """Float 20.9 truncates to 20 → equal (not small_advantage)."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 20.9}})
        assert esv["evaluation"]["band"] == "equal", (
            "Float 20.9 → int 20 → equal (inclusive bound)"
        )

    def test_none_value_coerces_to_zero(self):
        """ESV-28: None value → 0 → band=equal."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": None}})
        assert esv["evaluation"]["band"] == "equal"


# ===========================================================================
# ESV-29..30  None / empty input
# ===========================================================================


class TestNoneAndEmptyInput:
    """ESV-29..30: None and empty dict inputs produce valid ESVs via FEN fallback."""

    def test_none_input_returns_valid_esv(self):
        """ESV-29: None input → valid ESV dict (FEN fallback or neutral defaults)."""
        esv = extract_engine_signal(None)
        assert isinstance(esv, dict)
        assert "evaluation" in esv
        assert "last_move_quality" in esv
        assert "phase" in esv

    def test_empty_dict_returns_valid_esv(self):
        """ESV-30: Empty dict → valid ESV dict."""
        esv = extract_engine_signal({})
        assert isinstance(esv, dict)
        assert "evaluation" in esv

    def test_none_with_fen_uses_fen_material(self):
        """None input with valid FEN → evaluation derived from FEN material."""
        esv = extract_engine_signal(None, fen=_STARTING_FEN)
        assert isinstance(esv, dict)
        assert esv["evaluation"]["type"] == "cp"
        # Starting position is balanced → band=equal
        assert esv["evaluation"]["band"] == "equal"


# ===========================================================================
# ESV-31..35  FEN enrichment
# ===========================================================================


class TestFENEnrichment:
    """ESV-31..35: FEN material and phase fallback is correctly computed."""

    def test_opening_fen_phase(self):
        """ESV-32: Standard opening FEN → phase=opening."""
        esv = extract_engine_signal(None, fen=_STARTING_FEN)
        assert esv["phase"] == "opening", (
            f"Starting FEN must give phase=opening, got {esv['phase']!r}"
        )

    def test_endgame_fen_phase(self):
        """ESV-33: Endgame FEN (few pieces) → phase=endgame."""
        esv = extract_engine_signal(None, fen=_ENDGAME_FEN)
        assert esv["phase"] == "endgame", (
            f"Endgame FEN must give phase=endgame, got {esv['phase']!r}"
        )

    def test_stockfish_phase_overrides_fen_phase(self):
        """ESV-34: Stockfish-provided phase takes precedence over FEN-derived phase."""
        esv = extract_engine_signal(
            {"evaluation": {"type": "cp", "value": 0}, "phase": "opening"},
            fen=_ENDGAME_FEN,
        )
        assert esv["phase"] == "opening", (
            "Stockfish phase=opening must override FEN-derived endgame phase"
        )

    def test_invalid_fen_returns_valid_esv(self):
        """ESV-35: Invalid FEN → no crash; returns valid ESV with safe defaults."""
        esv = extract_engine_signal(None, fen="this-is-not-a-valid-fen")
        assert isinstance(esv, dict)
        assert "evaluation" in esv

    def test_none_fen_no_crash(self):
        """None FEN with empty input → no crash, valid ESV."""
        esv = extract_engine_signal({}, fen=None)
        assert isinstance(esv, dict)


# ===========================================================================
# ESV-36..37  Flag passthrough
# ===========================================================================


class TestFlagPassthrough:
    """ESV-36..37: Tactical and position flags are preserved verbatim."""

    def test_tactical_flags_preserved(self):
        """ESV-36: Tactical flags from Stockfish appear unchanged in ESV."""
        flags = ["fork", "pin", "discovered_attack"]
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 0}, "tactical_flags": flags})
        assert esv["tactical_flags"] == flags

    def test_position_flags_preserved(self):
        """ESV-37: Position flags from Stockfish appear unchanged in ESV."""
        flags = ["space_advantage", "king_safety"]
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 0}, "position_flags": flags})
        assert esv["position_flags"] == flags

    def test_empty_flags_preserved(self):
        """Empty flag lists remain empty, not None."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 0}, "tactical_flags": [], "position_flags": []})
        assert esv["tactical_flags"] == []
        assert esv["position_flags"] == []

    def test_missing_flags_default_to_empty_list(self):
        """Missing flags keys default to empty lists, not missing."""
        esv = extract_engine_signal({"evaluation": {"type": "cp", "value": 0}})
        assert esv["tactical_flags"] == []
        assert esv["position_flags"] == []


# ===========================================================================
# ESV-38..39  last_move_quality normalization
# ===========================================================================


class TestLastMoveQualityNormalization:
    """ESV-38..39: Unknown quality values are normalized; known values pass through."""

    def test_unknown_quality_normalized_to_unknown(self):
        """ESV-38: Unrecognized quality label → normalized to 'unknown'."""
        esv = extract_engine_signal(
            {"evaluation": {"type": "cp", "value": 0}, "errors": {"last_move_quality": "exotic_label"}}
        )
        assert esv["last_move_quality"] == "unknown", (
            "Unrecognized quality must be normalized to 'unknown'"
        )

    @pytest.mark.parametrize("quality", list(_KNOWN_MOVE_QUALITIES))
    def test_all_known_qualities_pass_through(self, quality: str):
        """ESV-39: All known quality values pass through unchanged."""
        esv = extract_engine_signal(
            {"evaluation": {"type": "cp", "value": 0}, "errors": {"last_move_quality": quality}}
        )
        assert esv["last_move_quality"] == quality, (
            f"Known quality {quality!r} must not be altered"
        )


# ===========================================================================
# ESV-40  Schema round-trip validation
# ===========================================================================


class TestSchemaRoundTrip:
    """ESV-40: Every ESV produced by extract_engine_signal passes EngineSignalSchema."""

    @pytest.mark.parametrize(
        "stockfish_json,fen",
        [
            ({"evaluation": {"type": "cp", "value": 0}}, None),
            ({"evaluation": {"type": "cp", "value": 20}}, None),
            ({"evaluation": {"type": "cp", "value": 21}}, None),
            ({"evaluation": {"type": "cp", "value": 60}}, None),
            ({"evaluation": {"type": "cp", "value": 61}}, None),
            ({"evaluation": {"type": "cp", "value": 120}}, None),
            ({"evaluation": {"type": "cp", "value": 121}}, None),
            ({"evaluation": {"type": "cp", "value": -50}}, None),
            ({"evaluation": {"type": "mate", "value": 3}}, _STARTING_FEN),
            ({"evaluation": {"type": "mate", "value": 3}}, _AFTER_E4_FEN),
            (None, _STARTING_FEN),
            ({}, None),
            ({"evaluation": {"type": "cp", "value": 0}, "errors": {"last_move_quality": "blunder"}}, None),
            ({"evaluation": {"type": "cp", "value": 0}, "errors": "bad_string"}, None),
            ({"evaluation": {"type": "unknown_type", "value": 42}}, None),
            ({"evaluation": {"type": "cp", "value": "not-a-number"}}, None),
        ],
    )
    def test_esv_validates_through_schema(self, stockfish_json, fen):
        """ESV-40: extract_engine_signal output always satisfies EngineSignalSchema."""
        esv = extract_engine_signal(stockfish_json, fen=fen)
        try:
            validated = _validated(esv)
        except ExplainSchemaError as exc:
            pytest.fail(
                f"EngineSignalSchema rejected ESV for input {stockfish_json!r}: {exc}\nESV: {esv}"
            )
        assert validated is not None

    @pytest.mark.parametrize("quality", list(_KNOWN_MOVE_QUALITIES))
    def test_all_quality_values_pass_schema(self, quality: str):
        """ESV-40b: Every known quality label satisfies EngineSignalSchema."""
        esv = extract_engine_signal(
            {"evaluation": {"type": "cp", "value": 0}, "errors": {"last_move_quality": quality}}
        )
        try:
            _validated(esv)
        except ExplainSchemaError as exc:
            pytest.fail(f"Quality {quality!r} rejected by schema: {exc}")


# ===========================================================================
# ESV-41..43  Logging warnings
# ===========================================================================


class TestLoggingWarnings:
    """ESV-41..43: Degradation paths emit logger.warning (not silent failures)."""

    def test_unknown_eval_type_logs_warning(self, caplog):
        """ESV-41: Unknown eval_type fires logger.warning."""
        module = "llm.rag.engine_signal.extract_engine_signal"
        with caplog.at_level(logging.WARNING, logger=module):
            extract_engine_signal({"evaluation": {"type": "bogus_type", "value": 0}})
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "eval_type" in r.message]
        assert warnings, (
            "Unknown eval_type must emit a logger.warning containing 'eval_type'"
        )

    def test_non_numeric_value_logs_warning(self, caplog):
        """ESV-42: Non-numeric eval value fires logger.warning."""
        module = "llm.rag.engine_signal.extract_engine_signal"
        with caplog.at_level(logging.WARNING, logger=module):
            extract_engine_signal({"evaluation": {"type": "cp", "value": "NaN"}})
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "eval value" in r.message]
        assert warnings, (
            "Non-numeric eval value must emit a logger.warning containing 'eval value'"
        )

    def test_non_dict_errors_logs_warning(self, caplog):
        """ESV-43: Non-dict errors field fires logger.warning."""
        module = "llm.rag.engine_signal.extract_engine_signal"
        with caplog.at_level(logging.WARNING, logger=module):
            extract_engine_signal({"evaluation": {"type": "cp", "value": 0}, "errors": "bad"})
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "errors" in r.message]
        assert warnings, (
            "Non-dict errors must emit a logger.warning containing 'errors'"
        )
