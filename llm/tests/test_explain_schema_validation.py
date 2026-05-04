"""Regression tests for LLM coaching response schema validation.

Tests verify that:
1. Valid responses (SAFE_V1 and LLM modes) pass schema validation.
2. Structurally malformed responses are rejected before reaching the client.
3. LLM explanations with forbidden content are blocked (Mode-2 rules).
4. Engine signal is validated independently of LLM text — the LLM cannot
   modify or inject evaluation results.
5. EmbeddedExplainResponse enforces confidence enum and non-empty explanation.

Contract: CI must fail if any schema check is weakened, schema fields are
removed, or the engine-signal immutability guard is disabled.
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from llm.rag.validators.explain_response_schema import (
    EmbeddedExplainResponse,
    EngineSignalSchema,
    ExplainResponse,
    ExplainSchemaError,
    validate_embedded_explain_response,
    validate_explain_response,
)

# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------


def _engine_signal(
    eval_type: str = "cp",
    band: str = "equal",
    side: str = "black",
    eval_delta: str = "stable",
    phase: str = "middlegame",
    tactical_flags: list | None = None,
    position_flags: list | None = None,
) -> dict:
    return {
        "evaluation": {"type": eval_type, "band": band, "side": side},
        "eval_delta": eval_delta,
        "last_move_quality": "unknown",
        "tactical_flags": tactical_flags if tactical_flags is not None else [],
        "position_flags": position_flags if position_flags is not None else [],
        "phase": phase,
    }


def _safe_response(explanation: str = "Position is roughly equal.") -> dict:
    return {
        "explanation": explanation,
        "engine_signal": _engine_signal(),
        "mode": "SAFE_V1",
    }


def _llm_response(explanation: str | None = None) -> dict:
    if explanation is None:
        explanation = (
            "White has a structural imbalance in the pawn formation. "
            "The rook is actively placed on the open file."
        )
    return {
        "explanation": explanation,
        "engine_signal": _engine_signal(
            eval_type="cp",
            band="small_advantage",
            side="white",
            eval_delta="increase",
            phase="middlegame",
            position_flags=["open_file"],
        ),
        "mode": "LLM_MODE_2",
    }


# ---------------------------------------------------------------
# Class 1: Structural schema validation (always applied)
# ---------------------------------------------------------------


class TestExplainResponseStructuralSchema:
    def test_valid_safe_v1_response_passes(self):
        result = validate_explain_response(_safe_response())
        assert isinstance(result, ExplainResponse)
        assert result.mode == "SAFE_V1"

    def test_valid_safe_v1_with_empty_explanation_passes(self):
        # SafeExplainer returns "" when engine_signal keys are absent;
        # SAFE_V1 mode does not require non-empty explanation.
        result = validate_explain_response(_safe_response(explanation=""))
        assert result.explanation == ""

    def test_valid_llm_response_passes(self):
        result = validate_explain_response(_llm_response())
        assert isinstance(result, ExplainResponse)
        assert result.mode == "LLM_MODE_2"

    def test_missing_explanation_field_rejected(self):
        r = _safe_response()
        del r["explanation"]
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_missing_engine_signal_rejected(self):
        r = _safe_response()
        del r["engine_signal"]
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_missing_mode_rejected(self):
        r = _safe_response()
        del r["mode"]
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_explanation_must_be_string_not_int(self):
        r = _safe_response()
        r["explanation"] = 42
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_explanation_must_be_string_not_none(self):
        r = _safe_response()
        r["explanation"] = None
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_explanation_must_be_string_not_list(self):
        r = _safe_response()
        r["explanation"] = ["sentence one", "sentence two"]
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_engine_signal_invalid_eval_type_rejected(self):
        r = _safe_response()
        r["engine_signal"]["evaluation"]["type"] = "pawn"
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_engine_signal_invalid_band_rejected(self):
        r = _safe_response()
        r["engine_signal"]["evaluation"]["band"] = "great_advantage"
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_engine_signal_invalid_side_rejected(self):
        r = _safe_response()
        r["engine_signal"]["evaluation"]["side"] = "neutral"
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_engine_signal_invalid_eval_delta_rejected(self):
        r = _safe_response()
        r["engine_signal"]["eval_delta"] = "flat"
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_engine_signal_invalid_phase_rejected(self):
        r = _safe_response()
        r["engine_signal"]["phase"] = "lategame"
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_engine_signal_tactical_flags_not_list_rejected(self):
        r = _safe_response()
        r["engine_signal"]["tactical_flags"] = "fork"
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_engine_signal_tactical_flags_non_strings_rejected(self):
        r = _safe_response()
        r["engine_signal"]["tactical_flags"] = [1, 2, 3]
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_engine_signal_position_flags_non_strings_rejected(self):
        r = _safe_response()
        r["engine_signal"]["position_flags"] = [True, False]
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_engine_signal_missing_evaluation_subfield_rejected(self):
        r = _safe_response()
        del r["engine_signal"]["evaluation"]["band"]
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_engine_signal_evaluation_not_a_dict_rejected(self):
        r = _safe_response()
        r["engine_signal"]["evaluation"] = "cp/equal/white"
        with pytest.raises(ExplainSchemaError, match="Structural schema"):
            validate_explain_response(r)

    def test_all_valid_eval_types_accepted(self):
        for eval_type in ("cp", "mate"):
            r = _safe_response()
            r["engine_signal"]["evaluation"]["type"] = eval_type
            if eval_type == "mate":
                r["engine_signal"]["evaluation"]["band"] = "decisive_advantage"
            result = validate_explain_response(r)
            assert result.engine_signal.evaluation.type == eval_type

    def test_all_valid_bands_accepted(self):
        for band in ("equal", "small_advantage", "clear_advantage", "decisive_advantage"):
            r = _safe_response()
            r["engine_signal"]["evaluation"]["band"] = band
            result = validate_explain_response(r)
            assert result.engine_signal.evaluation.band == band

    def test_all_valid_phases_accepted(self):
        for phase in ("opening", "middlegame", "endgame"):
            r = _safe_response()
            r["engine_signal"]["phase"] = phase
            result = validate_explain_response(r)
            assert result.engine_signal.phase == phase

    def test_tactical_flags_with_valid_strings_accepted(self):
        r = _safe_response()
        r["engine_signal"]["tactical_flags"] = ["fork", "pin", "hanging_piece"]
        result = validate_explain_response(r)
        assert result.engine_signal.tactical_flags == ["fork", "pin", "hanging_piece"]


# ---------------------------------------------------------------
# Class 2: Content validation for LLM modes
# ---------------------------------------------------------------


class TestExplainResponseContentValidation:
    def test_llm_mode_empty_explanation_rejected(self):
        r = _llm_response(explanation="")
        with pytest.raises(ExplainSchemaError, match="non-empty"):
            validate_explain_response(r)

    def test_llm_mode_whitespace_only_explanation_rejected(self):
        r = _llm_response(explanation="   \n  ")
        with pytest.raises(ExplainSchemaError, match="non-empty"):
            validate_explain_response(r)

    def test_llm_mode_speculative_language_rejected(self):
        r = _llm_response(explanation="White should advance the pawn.")
        with pytest.raises(ExplainSchemaError, match="Mode-2 content"):
            validate_explain_response(r)

    def test_llm_mode_chess_notation_rejected(self):
        # "Nf3" matches the forbidden notation pattern \b[KQRBN][a-h][1-8]\b
        r = _llm_response(explanation="The knight on Nf3 controls key squares.")
        with pytest.raises(ExplainSchemaError, match="Mode-2 content"):
            validate_explain_response(r)

    def test_llm_mode_mate_claim_rejected(self):
        r = _llm_response(explanation="The position leads to mate in 3 moves.")
        with pytest.raises(ExplainSchemaError, match="Mode-2 content"):
            validate_explain_response(r)

    def test_llm_mode_engine_reference_rejected(self):
        # "the engine wants" is in FORBIDDEN_PATTERNS
        r = _llm_response(explanation="The engine wants to capture the bishop.")
        with pytest.raises(ExplainSchemaError, match="Mode-2 content"):
            validate_explain_response(r)

    def test_llm_mode_calculation_language_rejected(self):
        r = _llm_response(explanation="One must calculate the variation carefully.")
        with pytest.raises(ExplainSchemaError, match="Mode-2 content"):
            validate_explain_response(r)

    def test_llm_mode_compliant_explanation_passes(self):
        r = _llm_response(
            explanation=(
                "White has a structural advantage in the endgame due to the passed pawn. "
                "The rook is actively posted and restricts the opposing king."
            )
        )
        result = validate_explain_response(r)
        assert result.mode == "LLM_MODE_2"

    def test_safe_v1_content_rules_not_applied(self):
        # SafeExplainer produces "best move is e2e4" – content validators
        # are intentionally NOT applied to SAFE_V1 mode.
        r = _safe_response(explanation="Best move is e2e4. Position is slightly better.")
        result = validate_explain_response(r)
        assert result.mode == "SAFE_V1"

    def test_safe_v1_empty_explanation_permitted(self):
        # SafeExplainer returns "" when all engine signal fields it reads
        # (eval_cp, best_move, etc.) are absent – this is expected behaviour.
        result = validate_explain_response(_safe_response(explanation=""))
        assert result.explanation == ""


# ---------------------------------------------------------------
# Class 3: Engine signal immutability
# ---------------------------------------------------------------


class TestEngineSignalImmutability:
    def test_engine_signal_schema_matches_extract_engine_signal_output(self):
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

        stockfish_json = {
            "evaluation": {"type": "cp", "value": 45},
            "eval_delta": 60,
            "tactical_flags": ["fork"],
            "position_flags": [],
            "phase": "middlegame",
            "errors": {"last_move_quality": "inaccuracy"},
        }
        esv = extract_engine_signal(
            stockfish_json, fen="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        )
        # Must validate without error
        validated = EngineSignalSchema.model_validate(esv)
        assert validated.evaluation.type == "cp"
        assert validated.eval_delta == "increase"

    def test_engine_signal_with_invalid_injected_type_rejected(self):
        # An LLM cannot override evaluation type with an arbitrary string.
        esv = _engine_signal()
        esv["evaluation"]["type"] = "llm_override"
        with pytest.raises(ValidationError):
            EngineSignalSchema.model_validate(esv)

    def test_engine_signal_with_injected_extra_fields_ignored(self):
        # Pydantic v2 ignores extra fields by default; existing contract fields
        # are unaffected by extra data an LLM might attempt to inject.
        esv = _engine_signal()
        esv["llm_injected"] = "override_value"
        validated = EngineSignalSchema.model_validate(esv)
        assert not hasattr(validated, "llm_injected")

    def test_response_engine_signal_equals_extracted_signal(self):
        # The engine_signal in the response must be the direct output of
        # extract_engine_signal() — not modified by the explanation text.
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

        fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        esv = extract_engine_signal({}, fen=fen)
        r = {
            "explanation": "The position is roughly balanced at the start.",
            "engine_signal": esv,
            "mode": "SAFE_V1",
        }
        validated = validate_explain_response(r)
        # Deep equality: schema-validated dict must match original ESV
        assert (
            validated.engine_signal.model_dump()
            == EngineSignalSchema.model_validate(esv).model_dump()
        )

    def test_engine_signal_integer_band_rejected(self):
        # band must be a string enum, not a numeric score.
        esv = _engine_signal()
        esv["evaluation"]["band"] = 120
        with pytest.raises(ValidationError):
            EngineSignalSchema.model_validate(esv)

    def test_engine_signal_none_phase_rejected(self):
        esv = _engine_signal()
        esv["phase"] = None
        with pytest.raises(ValidationError):
            EngineSignalSchema.model_validate(esv)

    def test_mate_engine_signal_schema(self):
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

        esv = extract_engine_signal(
            {"evaluation": {"type": "mate", "value": 3}},
            fen="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        )
        validated = EngineSignalSchema.model_validate(esv)
        assert validated.evaluation.type == "mate"
        assert validated.evaluation.band == "decisive_advantage"


# ---------------------------------------------------------------
# Class 4: EmbeddedExplainResponse schema
# ---------------------------------------------------------------


class TestEmbeddedExplainResponseSchema:
    def test_valid_embedded_high_confidence_passes(self):
        r = {
            "explanation": "The pawn structure is slightly imbalanced.",
            "confidence": "high",
            "tags": [],
        }
        result = validate_embedded_explain_response(r)
        assert result.confidence == "high"

    def test_valid_embedded_low_confidence_passes(self):
        r = {
            "explanation": "Evaluation unclear from available data.",
            "confidence": "low",
            "tags": ["partial"],
        }
        result = validate_embedded_explain_response(r)
        assert result.confidence == "low"

    def test_embedded_confidence_must_be_high_or_low(self):
        r = {"explanation": "Some text.", "confidence": "medium", "tags": []}
        with pytest.raises(ExplainSchemaError):
            validate_embedded_explain_response(r)

    def test_embedded_confidence_unknown_rejected(self):
        r = {"explanation": "Some text.", "confidence": "unknown", "tags": []}
        with pytest.raises(ExplainSchemaError):
            validate_embedded_explain_response(r)

    def test_embedded_empty_explanation_rejected(self):
        r = {"explanation": "", "confidence": "high", "tags": []}
        with pytest.raises(ExplainSchemaError):
            validate_embedded_explain_response(r)

    def test_embedded_whitespace_explanation_rejected(self):
        r = {"explanation": "   ", "confidence": "high", "tags": []}
        with pytest.raises(ExplainSchemaError):
            validate_embedded_explain_response(r)

    def test_embedded_missing_explanation_rejected(self):
        r = {"confidence": "high", "tags": []}
        with pytest.raises(ExplainSchemaError):
            validate_embedded_explain_response(r)

    def test_embedded_missing_confidence_rejected(self):
        r = {"explanation": "Some text.", "tags": []}
        with pytest.raises(ExplainSchemaError):
            validate_embedded_explain_response(r)

    def test_embedded_missing_tags_rejected(self):
        r = {"explanation": "Some text.", "confidence": "high"}
        with pytest.raises(ExplainSchemaError):
            validate_embedded_explain_response(r)

    def test_embedded_tags_non_strings_rejected(self):
        r = {"explanation": "Some text.", "confidence": "low", "tags": [1, 2]}
        with pytest.raises(ExplainSchemaError):
            validate_embedded_explain_response(r)

    def test_embedded_explanation_not_a_string_rejected(self):
        r = {"explanation": 42, "confidence": "high", "tags": []}
        with pytest.raises(ExplainSchemaError):
            validate_embedded_explain_response(r)

    def test_embedded_tags_empty_list_accepted(self):
        r = {"explanation": "Text.", "confidence": "high", "tags": []}
        result = validate_embedded_explain_response(r)
        assert result.tags == []

    def test_embedded_tags_valid_strings_accepted(self):
        r = {"explanation": "Text.", "confidence": "low", "tags": ["tactics", "pawn"]}
        result = validate_embedded_explain_response(r)
        assert result.tags == ["tactics", "pawn"]


# ---------------------------------------------------------------
# Class 5: ExplainResponse model direct access
# ---------------------------------------------------------------


class TestExplainResponseModelDirect:
    def test_pydantic_model_validates_nested_engine_signal(self):
        r = _safe_response()
        model = ExplainResponse.model_validate(r)
        assert model.engine_signal.evaluation.type == "cp"
        assert model.engine_signal.evaluation.band == "equal"
        assert model.engine_signal.phase == "middlegame"

    def test_pydantic_model_rejects_wrong_nested_type(self):
        r = _safe_response()
        r["engine_signal"]["evaluation"] = "not_a_dict"
        with pytest.raises(ValidationError):
            ExplainResponse.model_validate(r)

    def test_engine_signal_schema_standalone_valid(self):
        esv = _engine_signal(
            eval_type="cp",
            band="decisive_advantage",
            side="white",
            eval_delta="increase",
            phase="endgame",
            tactical_flags=["back_rank"],
        )
        validated = EngineSignalSchema.model_validate(esv)
        assert validated.tactical_flags == ["back_rank"]
        assert validated.phase == "endgame"

    def test_explain_response_mode_field_is_string(self):
        for mode in ("SAFE_V1", "LLM_MODE_2", "EXPERIMENTAL_V3"):
            r = _safe_response()
            r["mode"] = mode
            model = ExplainResponse.model_validate(r)
            assert model.mode == mode
