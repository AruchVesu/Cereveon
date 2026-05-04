"""Strict JSON schemas for the LLM-touching API responses.

Defines Pydantic models for structural validation and exposes
``validate_explain_response()``, ``validate_embedded_explain_response()``,
``validate_chat_response()``, and ``validate_live_move_response()`` as the
authoritative validation entry points used at the API boundary.

Design constraints:
- engine_signal is produced exclusively by extract_engine_signal() and is
  never sourced from or modified by LLM output.  The EngineSignalSchema
  enforces this contract structurally on every endpoint that returns one.
- For LLM-generated modes (anything other than SAFE_V1), the validators
  apply Mode-2 content rules (validate_mode_2_negative) on the user-facing
  text field — defence-in-depth over the per-generation validators already
  applied inside the chat / live-move pipelines.
- validate_embedded_explain_response() is the equivalent gate for the
  edge-deployment embedded.py path.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, ValidationError

# ---------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------


class ExplainSchemaError(ValueError):
    """Raised when an /explain response fails schema or content validation.

    At the server layer this propagates as HTTP 500 – a schema failure is a
    server-side programming error, not a client input error.
    """


# ---------------------------------------------------------------
# Inner schemas
# ---------------------------------------------------------------


class EvaluationSchema(BaseModel):
    """Mirrors the 'evaluation' sub-dict produced by extract_engine_signal."""

    type: Literal["cp", "mate"]
    band: Literal["equal", "small_advantage", "clear_advantage", "decisive_advantage"]
    side: Literal["white", "black", "unknown"]


class EngineSignalSchema(BaseModel):
    """Full engine signal structure as produced by extract_engine_signal().

    This schema is authoritative.  Any response whose engine_signal does not
    conform to this model is rejected before it reaches the Android client.
    Engine evaluation values are never sourced from LLM output.
    """

    evaluation: EvaluationSchema
    eval_delta: Literal["increase", "decrease", "stable"]
    last_move_quality: Literal[
        "unknown", "ok", "best", "excellent", "good", "inaccuracy", "mistake", "blunder"
    ]
    tactical_flags: list[str]
    position_flags: list[str]
    phase: Literal["opening", "middlegame", "endgame"]

    @field_validator("tactical_flags", "position_flags")
    @classmethod
    def validate_string_lists(cls, v: list) -> list:
        if not all(isinstance(item, str) for item in v):
            raise ValueError("all items must be strings")
        return v


# ---------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------


class ExplainResponse(BaseModel):
    """Schema for the POST /explain API response.

    engine_signal is produced exclusively by extract_engine_signal() and must
    pass EngineSignalSchema validation.  explanation is a plain string; it may
    be empty for SAFE_V1 (deterministic safe explainer path) but must be
    non-empty and Mode-2 compliant for all LLM-generated modes.
    """

    explanation: str
    engine_signal: EngineSignalSchema
    mode: str


class EmbeddedExplainResponse(BaseModel):
    """Schema for the embedded explain_position() API response.

    Used by the edge deployment (llm/rag/deploy/embedded.py).  confidence must
    be exactly 'high' or 'low' – no other values are valid contract-wise.
    """

    explanation: str
    confidence: Literal["high", "low"]
    tags: list[str]

    @field_validator("explanation")
    @classmethod
    def explanation_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("explanation must be non-empty")
        return v

    @field_validator("tags")
    @classmethod
    def tags_must_be_strings(cls, v: list) -> list:
        if not all(isinstance(t, str) for t in v):
            raise ValueError("all tags must be strings")
        return v


class ChatResponse(BaseModel):
    """Schema for the POST /chat (and /chat/stream pre-stream payload) API response.

    Mode is always ``CHAT_V1`` for this endpoint — the LLM-powered Mode-2
    coaching path with deterministic fallback.  Both branches must produce a
    non-empty reply that satisfies the Mode-2 negative validator.

    engine_signal is produced exclusively by extract_engine_signal() and must
    pass EngineSignalSchema validation.
    """

    reply: str
    engine_signal: EngineSignalSchema
    mode: Literal["CHAT_V1"]


class LiveMoveResponse(BaseModel):
    """Schema for the POST /live/move API response.

    Mode is always ``LIVE_V1``.  ``hint`` may be the empty string (per
    API_CONTRACTS.md §4 the deterministic-fallback path is allowed to emit
    an empty hint when no commentary is warranted), but when non-empty the
    Mode-2 negative validator applies.

    move_quality mirrors EngineSignalSchema.last_move_quality — the live
    pipeline propagates whichever quality bucket extract_engine_signal()
    assigned, including ``"unknown"`` when the engine signal lacks one.
    The narrower API_CONTRACTS.md §4 list ({good, inaccuracy, mistake,
    blunder}) is a documentation gap, not a code constraint.
    """

    status: Literal["ok"]
    hint: str
    engine_signal: EngineSignalSchema
    move_quality: Literal[
        "unknown", "ok", "best", "excellent", "good", "inaccuracy", "mistake", "blunder"
    ]
    mode: Literal["LIVE_V1"]


# ---------------------------------------------------------------
# Validation entry points
# ---------------------------------------------------------------


def validate_explain_response(response: dict) -> ExplainResponse:
    """Validate a /explain response dict against the structural schema and,
    for LLM-generated modes, against the Mode-2 content rules.

    Always enforces:
    - All required fields are present with correct types.
    - engine_signal matches EngineSignalSchema exactly (enum values, list
      element types, required nested fields).

    Additionally enforces for non-SAFE_V1 modes:
    - explanation is non-empty.
    - explanation passes validate_mode_2_negative (no forbidden patterns).

    Raises ExplainSchemaError with a descriptive message on any failure.
    Returns the validated ExplainResponse on success.
    """
    # --- Structural validation (always applied) ---
    try:
        validated = ExplainResponse.model_validate(response)
    except ValidationError as exc:
        raise ExplainSchemaError(f"Structural schema validation failed: {exc}") from exc

    # --- Content validation (only for LLM-generated modes) ---
    if validated.mode != "SAFE_V1":
        if not validated.explanation.strip():
            raise ExplainSchemaError(f"LLM explanation must be non-empty (mode={validated.mode!r})")
        from llm.rag.validators.mode_2_negative import validate_mode_2_negative

        try:
            validate_mode_2_negative(validated.explanation)
        except AssertionError as exc:
            raise ExplainSchemaError(
                f"Explanation failed Mode-2 content validation: {exc}"
            ) from exc

    return validated


def validate_embedded_explain_response(response: dict) -> EmbeddedExplainResponse:
    """Validate the embedded explain_position() response dict.

    Enforces:
    - explanation is present and non-empty.
    - confidence is exactly 'high' or 'low'.
    - tags is a list of strings.

    Raises ExplainSchemaError on failure.
    """
    try:
        return EmbeddedExplainResponse.model_validate(response)
    except ValidationError as exc:
        raise ExplainSchemaError(
            f"Embedded explain response schema validation failed: {exc}"
        ) from exc


def validate_chat_response(response: dict) -> ChatResponse:
    """Validate a /chat (or /chat/stream) response dict.

    Always enforces:
    - All required fields are present with correct types.
    - mode is exactly ``"CHAT_V1"``.
    - engine_signal matches EngineSignalSchema exactly.
    - reply is non-empty.
    - reply passes validate_mode_2_negative (no forbidden patterns,
      including invented chess moves, mate claims, and analysis language
      forbidden in Mode-2).

    The chat pipeline already runs validate_mode_2_negative inside the LLM
    retry loop and the OutputFirewall on top.  This validator is the
    boundary defence-in-depth seam: any future refactor that drops or
    weakens those internal checks will be caught here at the API edge.

    Raises ExplainSchemaError on failure.  Returns the validated
    ChatResponse on success.
    """
    try:
        validated = ChatResponse.model_validate(response)
    except ValidationError as exc:
        raise ExplainSchemaError(f"Chat response schema validation failed: {exc}") from exc

    if not validated.reply.strip():
        raise ExplainSchemaError("Chat reply must be non-empty")

    from llm.rag.validators.mode_2_negative import validate_mode_2_negative

    try:
        validate_mode_2_negative(validated.reply)
    except AssertionError as exc:
        raise ExplainSchemaError(f"Chat reply failed Mode-2 content validation: {exc}") from exc

    return validated


def validate_live_move_response(response: dict) -> LiveMoveResponse:
    """Validate a /live/move response dict.

    Always enforces:
    - All required fields are present with correct types.
    - status is exactly ``"ok"``, mode is exactly ``"LIVE_V1"``.
    - engine_signal matches EngineSignalSchema exactly.
    - move_quality is one of the EngineSignalSchema.last_move_quality bucket
      labels.

    For non-empty hints the Mode-2 negative validator applies — invented
    chess moves, mate claims, and forbidden analysis language are rejected.
    Empty hints pass through unchanged: API_CONTRACTS.md §4 explicitly
    permits the deterministic-fallback path to emit ``""`` and requires
    clients to preserve it as-is rather than substituting null.

    Raises ExplainSchemaError on failure.  Returns the validated
    LiveMoveResponse on success.
    """
    try:
        validated = LiveMoveResponse.model_validate(response)
    except ValidationError as exc:
        raise ExplainSchemaError(f"Live-move response schema validation failed: {exc}") from exc

    if validated.hint.strip():
        from llm.rag.validators.mode_2_negative import validate_mode_2_negative

        try:
            validate_mode_2_negative(validated.hint)
        except AssertionError as exc:
            raise ExplainSchemaError(
                f"Live-move hint failed Mode-2 content validation: {exc}"
            ) from exc

    return validated
