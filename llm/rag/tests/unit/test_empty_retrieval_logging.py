"""
Empty-retrieval telemetry pins for ``llm.rag.retriever.retriever``.

The ``docs/ARCHITECTURE.md`` > RAG Retrieval section documents the
"degraded but not refused" branch — when no rule-based document
matches, ``retrieve()`` returns ``[]`` and the prompt renders the
``(no retrieved context)`` placeholder.  ``llm/rag/documents/README.md``
> "When to add a document" names "Production telemetry shows a sustained
empty-retrieval rate on a specific ESV signature" as the canonical
trigger for corpus expansion.

This file pins the instrumentation that produces that telemetry:

  ERL_EMIT_*    Empty retrieval emits exactly one INFO log with the
                ``"RAG empty retrieval"`` message prefix.
  ERL_QUIET_*   Non-empty retrieval emits ZERO such logs (so the
                signal is unambiguous in production).
  ERL_SHAPE_*   The signature carries exactly six keys, in the order
                the corpus-coverage gaps in README.md are organised.
  ERL_DET_*     The signature is deterministic across calls and
                across equivalent flag-orderings — two ESVs differing
                only in tactical-flag order map to the same signature.
  ERL_EXCL_*   ``evaluation.side`` and ``eval_delta`` are absent —
                no live document conditions on either, so including
                them in the signature would inflate cardinality
                without informing corpus decisions.
  ERL_JSON_*    The signature is JSON-serializable so downstream log
                backends can index it.
"""

from __future__ import annotations

import json
import logging

import pytest

from llm.rag.retriever.retriever import (
    _empty_retrieval_signature,
    retrieve,
)

_MESSAGE_PREFIX = "RAG empty retrieval"


# ---------------------------------------------------------------------------
# Fixture data — a minimal ESV that matches no document, and a minimal
# document that matches a synthetic ESV.  Independent of the live
# corpus so a corpus expansion (which IS the eventual outcome of this
# telemetry) cannot regress these tests.
# ---------------------------------------------------------------------------


def _empty_esv() -> dict:
    """ESV the live corpus does not cover (clear_advantage band, no flags)."""
    return {
        "evaluation": {"type": "cp", "band": "clear_advantage", "side": "white"},
        "eval_delta": "stable",
        "last_move_quality": "inaccuracy",
        "phase": "middlegame",
        "tactical_flags": [],
        "position_flags": [],
    }


def _matching_esv() -> dict:
    """ESV that matches the synthetic doc below."""
    return {
        "evaluation": {"type": "cp", "band": "small_advantage", "side": "white"},
        "eval_delta": "stable",
        "last_move_quality": "ok",
        "phase": "opening",
        "tactical_flags": [],
        "position_flags": [],
    }


def _synthetic_doc() -> dict:
    return {
        "id": "synthetic_small_advantage",
        "type": "evaluation_translation",
        "conditions": {"evaluation.band": "small_advantage"},
        "content": {"description": "synthetic"},
    }


# ---------------------------------------------------------------------------
# ERL_EMIT — empty retrieval emits exactly one INFO log.
# ---------------------------------------------------------------------------


def test_ERL_EMIT_empty_retrieval_emits_info_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Empty retrieval MUST emit exactly one log record with the
    documented message prefix at INFO level.  Pinned by exact-message
    match plus level so a future contributor cannot quietly downgrade
    to debug or remove the call."""
    with caplog.at_level(logging.INFO, logger="llm.rag.retriever.retriever"):
        result = retrieve(_empty_esv(), documents=[_synthetic_doc()])
    assert result == [], "fixture ESV must produce empty retrieval"

    empties = [
        rec
        for rec in caplog.records
        if rec.name == "llm.rag.retriever.retriever" and rec.message.startswith(_MESSAGE_PREFIX)
    ]
    assert len(empties) == 1, (
        f"Expected exactly one empty-retrieval log record; got "
        f"{[(r.levelname, r.message) for r in empties]!r}."
    )
    assert empties[0].levelno == logging.INFO, (
        f"Empty retrieval must log at INFO (degraded-but-correct), " f"got {empties[0].levelname}."
    )


def test_ERL_EMIT_log_record_carries_signature_in_extra(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The structured ``extra={"esv_signature": ...}`` payload MUST
    reach LogRecord attribute access so downstream backends (Loki,
    CloudWatch Insights) can index per-field.  Pins the contract
    ``logger.info(..., extra={"esv_signature": <dict>})``."""
    with caplog.at_level(logging.INFO, logger="llm.rag.retriever.retriever"):
        retrieve(_empty_esv(), documents=[_synthetic_doc()])
    [rec] = [
        rec
        for rec in caplog.records
        if rec.name == "llm.rag.retriever.retriever" and rec.message.startswith(_MESSAGE_PREFIX)
    ]
    sig = getattr(rec, "esv_signature", None)
    assert isinstance(sig, dict), (
        "LogRecord is missing the structured 'esv_signature' attribute. "
        "Confirm logger.info(..., extra={'esv_signature': signature}) is "
        "in retrieve()."
    )
    assert sig == _empty_retrieval_signature(_empty_esv())


# ---------------------------------------------------------------------------
# ERL_QUIET — non-empty retrieval emits ZERO empty-retrieval logs.
# ---------------------------------------------------------------------------


def test_ERL_QUIET_non_empty_retrieval_emits_no_empty_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When ``retrieve()`` returns at least one document, the
    empty-retrieval branch MUST NOT fire.  Pins the ``if not matched:``
    guard — a future "log every retrieve" widening would have to
    delete this test."""
    with caplog.at_level(logging.INFO, logger="llm.rag.retriever.retriever"):
        result = retrieve(_matching_esv(), documents=[_synthetic_doc()])
    assert result, "fixture ESV must produce non-empty retrieval"

    empties = [
        rec
        for rec in caplog.records
        if rec.name == "llm.rag.retriever.retriever" and rec.message.startswith(_MESSAGE_PREFIX)
    ]
    assert not empties, (
        f"Non-empty retrieval emitted unexpected empty-retrieval logs: "
        f"{[r.message for r in empties]!r}."
    )


# ---------------------------------------------------------------------------
# ERL_SHAPE — signature contains exactly six keys.
# ---------------------------------------------------------------------------


def test_ERL_SHAPE_signature_has_exactly_six_keys() -> None:
    """The signature MUST contain exactly the six bucketing keys that
    correspond to the README.md coverage-gap axes.  Adding a new key
    requires updating the corpus README AND this test in the same
    commit — log backends already indexing on the existing keys
    shouldn't see schema drift."""
    sig = _empty_retrieval_signature(_empty_esv())
    assert set(sig.keys()) == {
        "evaluation_type",
        "evaluation_band",
        "last_move_quality",
        "phase",
        "tactical_flags",
        "position_flags",
    }, (
        f"Signature key set drifted from the documented six; got " f"{sorted(sig.keys())}."
    )


# ---------------------------------------------------------------------------
# ERL_DET — signature is deterministic across equivalent ESVs.
# ---------------------------------------------------------------------------


def test_ERL_DET_signature_is_stable_across_calls() -> None:
    """Repeated calls with the same ESV produce identical signatures.
    Catches an accidental introduction of non-deterministic ordering
    (e.g. dict iteration on a set-derived list, time-based field)."""
    esv = _empty_esv()
    sig_a = _empty_retrieval_signature(esv)
    sig_b = _empty_retrieval_signature(esv)
    assert sig_a == sig_b


def test_ERL_DET_signature_invariant_under_flag_reordering() -> None:
    """ESVs differing only in tactical / positional flag order MUST
    map to the same signature.  This is the property that lets log
    aggregation bucket them together — without sorted lists, the
    same coverage gap would appear under two different keys depending
    on which order Stockfish happened to emit the flags."""
    a = _empty_esv()
    a["tactical_flags"] = ["fork", "pin"]
    a["position_flags"] = ["open_file", "passed_pawn"]
    b = _empty_esv()
    b["tactical_flags"] = ["pin", "fork"]
    b["position_flags"] = ["passed_pawn", "open_file"]
    assert _empty_retrieval_signature(a) == _empty_retrieval_signature(b)


# ---------------------------------------------------------------------------
# ERL_EXCL — side and eval_delta are NOT in the signature.
# ---------------------------------------------------------------------------


def test_ERL_EXCL_evaluation_side_absent_from_signature() -> None:
    """``evaluation.side`` MUST NOT appear in the signature: no live
    document conditions on side, so including it would split every
    bucket in two without informing corpus decisions.  Removing this
    test requires removing the corresponding ``_SIGNATURE_FIELDS_*``
    constant entry in retriever.py."""
    a = _empty_esv()
    a["evaluation"] = {**a["evaluation"], "side": "white"}
    b = _empty_esv()
    b["evaluation"] = {**b["evaluation"], "side": "black"}
    assert _empty_retrieval_signature(a) == _empty_retrieval_signature(b), (
        "Signature should be invariant under evaluation.side — "
        "if you intentionally added side to the bucketing key, "
        "remove this test and document the cardinality cost."
    )


def test_ERL_EXCL_eval_delta_absent_from_signature() -> None:
    """``eval_delta`` MUST NOT appear in the signature, same rationale
    as side: no live document conditions on it."""
    a = _empty_esv()
    a["eval_delta"] = "increase"
    b = _empty_esv()
    b["eval_delta"] = "decrease"
    assert _empty_retrieval_signature(a) == _empty_retrieval_signature(b)


# ---------------------------------------------------------------------------
# ERL_JSON — signature is JSON-serializable.
# ---------------------------------------------------------------------------


def test_ERL_JSON_signature_is_json_serializable() -> None:
    """Log backends that pick up ``extra=`` typically JSON-encode the
    structured payload.  A signature that contains a non-encodable
    type (set, tuple of dicts, custom class) would silently lose the
    structured indexing.  Roundtrip through ``json.dumps`` →
    ``json.loads`` catches that."""
    sig = _empty_retrieval_signature(_empty_esv())
    encoded = json.dumps(sig, sort_keys=True)
    decoded = json.loads(encoded)
    # sorted lists round-trip as lists; None as null; primitives as primitives.
    assert decoded == sig


def test_ERL_JSON_signature_handles_missing_fields_gracefully() -> None:
    """A hand-built ESV missing some fields (defensive, since the
    extractor populates them all) MUST produce a signature with
    ``None`` for missing scalars and ``[]`` for missing lists — not
    raise, not crash, not produce ``"None"`` strings."""
    sparse_esv = {"evaluation": {}}  # everything else missing
    sig = _empty_retrieval_signature(sparse_esv)
    assert sig["evaluation_type"] is None
    assert sig["evaluation_band"] is None
    assert sig["last_move_quality"] is None
    assert sig["phase"] is None
    assert sig["tactical_flags"] == []
    assert sig["position_flags"] == []


def test_ERL_JSON_signature_handles_non_dict_evaluation_gracefully() -> None:
    """An ESV with a non-dict ``evaluation`` field (defensive — the
    extractor never produces this, but the trust-boundary should not
    crash on hostile shapes) MUST yield ``None`` for evaluation fields,
    not raise AttributeError."""
    weird_esv = {"evaluation": "not-a-dict", "tactical_flags": []}
    sig = _empty_retrieval_signature(weird_esv)
    assert sig["evaluation_type"] is None
    assert sig["evaluation_band"] is None
