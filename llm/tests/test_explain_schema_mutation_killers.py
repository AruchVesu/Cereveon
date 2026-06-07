"""Mutation-killer tests for ``llm/rag/validators/explain_response_schema.py``.

Sprint 6.D — the file was added to the mutmut target list in the
refresh pass, and its initial kill rate was 22% (8 of 36 mutants
killed by the existing boundary tests).  Most survivors were:

  - Pydantic ``field_validator`` decorator mutations: the existing
    tests round-tripped happy payloads through ``model_validate`` and
    never sent the malformed shapes that the field validators are
    supposed to catch (non-string list items, empty explanation,
    non-string tags).  When mutmut mutated the decorator's field name
    to a typo, the validator became dead code — but the suite passed.

  - Format-string mutations in branch-specific error messages: the
    existing tests used ``match="Mode-2"`` which is a substring match
    that survives almost every per-word mutation of the surrounding
    phrase.  Tightening to ``match="Chat reply failed Mode-2 content
    validation"`` (and so on for structure / semantic / schema /
    empty branches) kills the prefix and per-word mutations.

Each test below targets exactly ONE branch.  The input is constructed
so the other branches do NOT fire — without this, a test that
"triggers Mode-2 content" might also trigger Mode-2 semantic, and we
couldn't tell which message-mutant survived.

Stable test IDs (do NOT rename):
  MK_ESL_*    EngineSignalSchema.validate_string_lists (tactical_flags, position_flags)
  MK_EER_*    EmbeddedExplainResponse field validators
  MK_VER_*    validate_explain_response branches (schema / mode / empty / content)
  MK_VCR_*    validate_chat_response branches (schema / empty / content / structure / semantic)
  MK_VLM_*    validate_live_move_response branches (schema / content / structure / semantic)
"""

from __future__ import annotations

import pytest

from llm.rag.validators.explain_response_schema import (
    ExplainSchemaError,
    validate_chat_response,
    validate_embedded_explain_response,
    validate_explain_response,
    validate_live_move_response,
)


def _engine_signal(band: str = "small_advantage", tactical_flags: list | None = None) -> dict:
    """Default engine signal that satisfies EngineSignalSchema.  Override
    band/tactical_flags to steer the Mode-2 semantic checks (equal-band
    forbids "better"/"winning"/etc; empty tactical_flags forbids
    "fork"/"pin"/etc)."""
    return {
        "evaluation": {"type": "cp", "band": band, "side": "white"},
        "eval_delta": "stable",
        "last_move_quality": "good",
        "tactical_flags": tactical_flags if tactical_flags is not None else [],
        "position_flags": [],
        "phase": "middlegame",
    }


# ---------------------------------------------------------------------------
# MK_ESL_*  EngineSignalSchema list-element enforcement (Pydantic schema layer)
# ---------------------------------------------------------------------------
#
# The earlier ``validate_string_lists`` @field_validator was deleted in
# the Sprint 6.D pass — Pydantic v2 enforces ``list[str]`` element typing
# at the schema layer before any field validator runs, so the custom
# validator was unreachable dead code (one of the mutation findings).
# The contract it claimed to enforce now lives at the schema layer; these
# tests pin that surfacing path so a future regression that loosens the
# ``list[str]`` type (e.g. to ``list``) fails loudly.


def test_mk_esl_01_tactical_flags_non_string_raises_schema_layer():
    """MK_ESL_01 — Pydantic schema enforcement: non-string items in
    tactical_flags surface as a schema validation failure (wrapped in
    ExplainSchemaError).  Pinned by inspecting the wrapped message so
    a regression that loosens the field type fails loudly here."""
    payload = {
        "reply": "The position is balanced.",
        "engine_signal": {**_engine_signal(), "tactical_flags": [1, 2]},
        "mode": "CHAT_V1",
    }
    with pytest.raises(ExplainSchemaError) as exc_info:
        validate_chat_response(payload)
    message = str(exc_info.value)
    assert "Chat response schema validation failed" in message
    assert "tactical_flags" in message


def test_mk_esl_02_position_flags_non_string_raises_schema_layer():
    """MK_ESL_02 — same enforcement for position_flags."""
    payload = {
        "reply": "The position is balanced.",
        "engine_signal": {**_engine_signal(), "position_flags": [42]},
        "mode": "CHAT_V1",
    }
    with pytest.raises(ExplainSchemaError) as exc_info:
        validate_chat_response(payload)
    message = str(exc_info.value)
    assert "Chat response schema validation failed" in message
    assert "position_flags" in message


# ---------------------------------------------------------------------------
# MK_EER_*  EmbeddedExplainResponse field validators
# ---------------------------------------------------------------------------


def test_mk_eer_01_empty_explanation_raises_non_empty():
    """MK_EER_01 — empty explanation must surface ``explanation must be
    non-empty`` from the field validator.  Pydantic wraps the
    ValueError into a ValidationError, which the outer
    ``validate_embedded_explain_response`` rethrows as
    ``Embedded explain response schema validation failed: <wrapped>``,
    so the inner phrase lives in the body of the outer message.  Two
    assertions kill three mutant classes:

      - outer-prefix mutation on line 219 → fails the ``^Embedded...``
        anchor.
      - inner-message mutation on line 113 (the ValueError text) →
        fails the body assertion.
      - decorator dead-code mutation on lines 108-109 → produces no
        exception at all (Pydantic accepts the empty string) and
        therefore fails ``pytest.raises``.
    """
    payload = {
        "explanation": "   ",
        "confidence": "high",
        "tags": ["opening", "white"],
    }
    with pytest.raises(
        ExplainSchemaError, match=r"^Embedded explain response schema validation failed"
    ) as exc_info:
        validate_embedded_explain_response(payload)
    assert "explanation must be non-empty" in str(exc_info.value)


def test_mk_eer_02_non_string_tags_raises_at_schema_layer():
    """MK_EER_02 — non-string items in tags surface as a Pydantic
    schema validation failure (the earlier ``tags_must_be_strings``
    @field_validator was dead code — see MK_ESL_* block comment).
    Pinned via the schema-failure wrap-around message so a regression
    that loosens the ``tags: list[str]`` annotation fails loudly."""
    payload = {
        "explanation": "Black has a slight advantage on the queenside.",
        "confidence": "high",
        "tags": ["opening", 7],
    }
    with pytest.raises(ExplainSchemaError) as exc_info:
        validate_embedded_explain_response(payload)
    message = str(exc_info.value)
    assert "Embedded explain response schema validation failed" in message
    assert "tags" in message


def test_mk_eer_03_bad_confidence_raises_schema_failure():
    """MK_EER_03 — confidence outside {'high', 'low'} fails the
    Literal[] check at the Pydantic layer.  Surfaces via the
    "Embedded explain response schema validation failed" wrap-around
    error message (line 218); kills mutations on that prefix."""
    payload = {
        "explanation": "Black has a slight advantage on the queenside.",
        "confidence": "medium",  # not in Literal["high", "low"]
        "tags": [],
    }
    with pytest.raises(
        ExplainSchemaError, match=r"^Embedded explain response schema validation failed"
    ):
        validate_embedded_explain_response(payload)


# ---------------------------------------------------------------------------
# MK_VER_*  validate_explain_response branches
# ---------------------------------------------------------------------------


def test_mk_ver_01_bad_structure_raises_structural_schema():
    """MK_VER_01 — missing required field surfaces with "Structural
    schema validation failed".  Kills the prefix mutations on
    line 187."""
    payload = {
        "explanation": "Some explanation",
        # engine_signal intentionally missing
        "mode": "EXPLAIN_V1",
    }
    with pytest.raises(ExplainSchemaError, match=r"^Structural schema validation failed"):
        validate_explain_response(payload)


def test_mk_ver_02_safe_v1_mode_skips_content_check():
    """MK_VER_02 — mode=SAFE_V1 must SKIP the empty/content gate so the
    deterministic fallback path can emit ``""`` without crashing.

    Kills the mutation ``if validated.mode != "SAFE_V1"`` → ``if True``
    (would force content check, breaking the SAFE_V1 contract).  Pin
    by sending an empty explanation under SAFE_V1 mode and confirming
    no exception is raised.
    """
    payload = {
        "explanation": "",  # would fail content check if mode != SAFE_V1
        "engine_signal": _engine_signal(),
        "mode": "SAFE_V1",
    }
    # No exception.
    validated = validate_explain_response(payload)
    assert validated.mode == "SAFE_V1"
    assert validated.explanation == ""


def test_mk_ver_03_non_safe_mode_empty_explanation_raises_specific_phrase():
    """MK_VER_03 — mode != SAFE_V1 + empty explanation raises with the
    full "LLM explanation must be non-empty" phrase.  Kills any
    mutation on words inside that string."""
    payload = {
        "explanation": "   ",
        "engine_signal": _engine_signal(),
        "mode": "EXPLAIN_V1",
    }
    with pytest.raises(ExplainSchemaError, match=r"^LLM explanation must be non-empty"):
        validate_explain_response(payload)


def test_mk_ver_04_non_safe_mode_forbidden_content_raises_explanation_failed():
    """MK_VER_04 — content failure surfaces with "Explanation failed
    Mode-2 content validation".  Kills mutations on every word of
    that prefix (otherwise ``match="Mode-2"`` would survive a swap
    of "content" → "structure")."""
    payload = {
        "explanation": "The position is checkmate.",  # triggers mode_2_negative
        "engine_signal": _engine_signal(),
        "mode": "EXPLAIN_V1",
    }
    with pytest.raises(
        ExplainSchemaError, match=r"^Explanation failed Mode-2 content validation"
    ):
        validate_explain_response(payload)


# ---------------------------------------------------------------------------
# MK_VCR_*  validate_chat_response branches
# ---------------------------------------------------------------------------


def _chat_payload(**overrides) -> dict:
    base = {
        "reply": "The position is balanced; both sides have active pieces.",
        "engine_signal": _engine_signal(),
        "mode": "CHAT_V1",
    }
    base.update(overrides)
    return base


def test_mk_vcr_01_schema_branch_emits_specific_phrase():
    """MK_VCR_01 — bad shape surfaces "Chat response schema validation
    failed".  Kills mutations on that exact prefix."""
    with pytest.raises(ExplainSchemaError, match=r"^Chat response schema validation failed"):
        validate_chat_response(_chat_payload(mode="EXPLAIN_V1"))


def test_mk_vcr_02_empty_reply_emits_specific_phrase():
    """MK_VCR_02 — whitespace-only reply surfaces "Chat reply must be
    non-empty".  Kills mutations on every word of that phrase."""
    with pytest.raises(ExplainSchemaError, match=r"^Chat reply must be non-empty"):
        validate_chat_response(_chat_payload(reply="   "))


def test_mk_vcr_03_content_branch_emits_specific_phrase():
    """MK_VCR_03 — content failure raises "Chat reply failed Mode-2
    content validation".  Triggered by ``checkmate`` (mode_2_negative
    forbids it).  No "plan"/"consider" in the reply so structure
    doesn't fire; no engine-speculation words so semantic doesn't
    fire."""
    with pytest.raises(
        ExplainSchemaError, match=r"^Chat reply failed Mode-2 content validation"
    ):
        validate_chat_response(_chat_payload(reply="The position is checkmate."))


def test_mk_vcr_04_structure_branch_emits_specific_phrase():
    """MK_VCR_04 — structure failure raises "Chat reply failed Mode-2
    structure validation".  Triggered by the prescriptive ``Recommended
    move:`` section (mode_2_structure forbids ``\\brecommended move\\b``).
    Was a "Plan:" header until 2026-06-07, when "plan" was fully retired
    from MOVE_ADVISORY_PATTERNS — the header word is harmless on its own;
    see test_structure_plan_unlock.py.  Verified to NOT trigger content
    (no chess moves, no checkmate, no "I think") and NOT trigger semantic
    (no equal-band words, no engine speculation, no invented-tactic
    words)."""
    with pytest.raises(
        ExplainSchemaError, match=r"^Chat reply failed Mode-2 structure validation"
    ):
        validate_chat_response(
            _chat_payload(reply="Recommended move: improve the pieces and contest the centre.")
        )


def test_mk_vcr_05_semantic_branch_emits_specific_phrase():
    """MK_VCR_05 — semantic failure raises "Chat reply failed Mode-2
    semantic validation".  Triggered by ``winning`` in an ``equal``-
    band context (mode_2_semantic forbids equal-band advantage
    language).  Was ``better`` until 2026-06-07, when "better" was
    retired from EQUAL_ADVANTAGE_WORDS (too common a comparative); see
    test_semantic_strategic_vocab_unlock.py.  Reply has no chess moves /
    mate / engine words / advisory sections, so it passes content +
    structure."""
    with pytest.raises(
        ExplainSchemaError, match=r"^Chat reply failed Mode-2 semantic validation"
    ):
        validate_chat_response(
            _chat_payload(
                reply="Both sides look balanced, but Black is winning.",
                engine_signal=_engine_signal(band="equal"),
            )
        )


# ---------------------------------------------------------------------------
# MK_VLM_*  validate_live_move_response branches
# ---------------------------------------------------------------------------


def _live_move_payload(**overrides) -> dict:
    base = {
        "status": "ok",
        "hint": "The position is balanced; both sides have active pieces.",
        "engine_signal": _engine_signal(),
        "move_quality": "good",
        "mode": "LIVE_V1",
    }
    base.update(overrides)
    return base


def test_mk_vlm_01_schema_branch_emits_specific_phrase():
    """MK_VLM_01 — bad shape surfaces "Live-move response schema
    validation failed"."""
    with pytest.raises(
        ExplainSchemaError, match=r"^Live-move response schema validation failed"
    ):
        validate_live_move_response(_live_move_payload(mode="CHAT_V1"))


def test_mk_vlm_02_content_branch_emits_specific_phrase():
    """MK_VLM_02 — content failure raises "Live-move hint failed
    Mode-2 content validation"."""
    with pytest.raises(
        ExplainSchemaError, match=r"^Live-move hint failed Mode-2 content validation"
    ):
        validate_live_move_response(_live_move_payload(hint="The position is checkmate."))


def test_mk_vlm_03_structure_branch_emits_specific_phrase():
    """MK_VLM_03 — structure failure raises "Live-move hint failed
    Mode-2 structure validation".  Triggered by the prescriptive
    ``Recommended move:`` section (``\\brecommended move\\b``).  Was a
    "Plan:" header until 2026-06-07, when "plan" was fully retired from
    MOVE_ADVISORY_PATTERNS (header word + bare noun both accepted now);
    see test_structure_plan_unlock.py."""
    with pytest.raises(
        ExplainSchemaError, match=r"^Live-move hint failed Mode-2 structure validation"
    ):
        validate_live_move_response(
            _live_move_payload(hint="Recommended move: improve the pieces and contest the centre.")
        )


def test_mk_vlm_04_semantic_branch_emits_specific_phrase():
    """MK_VLM_04 — semantic failure raises "Live-move hint failed
    Mode-2 semantic validation".  Triggered by ``winning`` on an
    ``equal`` band.  Was ``better`` until 2026-06-07, when "better" was
    retired from EQUAL_ADVANTAGE_WORDS; see
    test_semantic_strategic_vocab_unlock.py."""
    with pytest.raises(
        ExplainSchemaError, match=r"^Live-move hint failed Mode-2 semantic validation"
    ):
        validate_live_move_response(
            _live_move_payload(
                hint="Both sides look balanced, but Black is winning.",
                engine_signal=_engine_signal(band="equal"),
            )
        )


def test_mk_vlm_05_empty_hint_skips_mode_2_checks():
    """MK_VLM_05 — empty hint passes through (API_CONTRACTS.md §4
    deterministic-fallback contract).  Pins the
    ``if validated.hint.strip()`` gate so a mutation that drops the
    guard would force content/structure/semantic on an empty string
    and break the fallback path."""
    result = validate_live_move_response(_live_move_payload(hint=""))
    assert result.hint == ""
