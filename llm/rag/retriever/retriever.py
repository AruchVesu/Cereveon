# rag/retriever/retriever.py

import logging

from .priority import PRIORITY_MAP
from .rule_matcher import matches_conditions

MAX_DOCS = 7

logger = logging.getLogger(__name__)

# Fields included in the empty-retrieval ESV signature.  The set is
# intentionally narrow: every entry corresponds to a coverage-gap axis
# documented in ``llm/rag/documents/README.md`` > "Known coverage
# gaps", so log aggregation by these fields directly answers "what
# document is missing".  Two ESV fields are deliberately excluded —
# ``evaluation.side`` and ``eval_delta`` — because no document in the
# live corpus conditions on them (per the same README), so they have
# zero bucketing value and would only inflate cardinality.
#
# Pinned by ``test_empty_retrieval_logging``.
_SIGNATURE_FIELDS_NESTED: tuple[tuple[str, ...], ...] = (
    ("evaluation", "type"),
    ("evaluation", "band"),
)
_SIGNATURE_FIELDS_SCALAR: tuple[str, ...] = (
    "last_move_quality",
    "phase",
)
_SIGNATURE_FIELDS_LIST: tuple[str, ...] = (
    "tactical_flags",
    "position_flags",
)


def _empty_retrieval_signature(esv: dict) -> dict:
    """Build the structured bucketing key for an empty-retrieval event.

    Output is deterministic for a given ESV: scalar fields read through,
    list fields are sorted so two ESVs differing only in flag-order map
    to the same bucket.  ``None`` is returned for any missing field
    rather than the literal string ``"None"`` — log backends that
    distinguish nulls (Loki, CloudWatch Insights) keep that
    distinction.

    Pinned shape (six keys):
      evaluation_type   "cp" | "mate" | None
      evaluation_band   "equal" | "small_advantage" | "clear_advantage" | "decisive_advantage" | None
      last_move_quality "unknown" | "ok" | "best" | ... | None
      phase             "opening" | "middlegame" | "endgame" | None
      tactical_flags    sorted list[str]
      position_flags    sorted list[str]
    """
    sig: dict = {}
    for top, leaf in _SIGNATURE_FIELDS_NESTED:
        nested = esv.get(top, {})
        if not isinstance(nested, dict):
            nested = {}
        sig[f"{top}_{leaf}"] = nested.get(leaf)
    for field in _SIGNATURE_FIELDS_SCALAR:
        sig[field] = esv.get(field)
    for field in _SIGNATURE_FIELDS_LIST:
        raw = esv.get(field, [])
        # ``_filter_flags`` upstream guarantees this is a list of safe
        # strings; the ``isinstance`` check is defence in depth in case
        # a future caller invokes ``retrieve()`` with a hand-built ESV
        # that skipped that filter.
        sig[field] = sorted(raw) if isinstance(raw, list) else []
    return sig


def retrieve(esv: dict, documents: list) -> list:
    matched = []

    for doc in documents:
        conditions = doc.get("conditions", {})
        if matches_conditions(esv, conditions):
            matched.append(doc)

    matched.sort(key=lambda d: PRIORITY_MAP.get(d.get("type", ""), 99))

    if not matched:
        # The "degraded but not refused" branch from
        # ``docs/ARCHITECTURE.md`` > RAG Retrieval.  Emit one
        # structured INFO line per occurrence so production telemetry
        # can answer "which ESV signatures are hitting the empty
        # branch most often", which is the canonical trigger for
        # adding a new conditioned document (``llm/rag/documents/
        # README.md`` > "When to add a document").
        #
        # INFO (not WARNING) because empty retrieval is the documented
        # degraded-but-correct path, not a failure — the validators
        # still enforce every safety claim downstream.  ``extra=`` is
        # populated so structured backends index per-field; the
        # message string carries the same dict so plain-text grep
        # also works.
        signature = _empty_retrieval_signature(esv)
        logger.info(
            "RAG empty retrieval esv_signature=%s",
            signature,
            extra={"esv_signature": signature},
        )

    return matched[:MAX_DOCS]
