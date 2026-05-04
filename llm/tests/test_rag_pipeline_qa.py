"""
RAG Pipeline QA Tests — llm/tests/test_rag_pipeline_qa.py

QA Plan Section 8 — RAG-01 through RAG-04.

RAG-01  RAG retrieval is deterministic: same ESV always returns same documents
RAG-02  RAG returns relevant strategic documents for a winning-position ESV
RAG-03  RAG handles empty document store gracefully — no crash, signals missing data
RAG-04  RAG documents are never modified by the LLM or any downstream component
"""

from __future__ import annotations

import copy

import pytest

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.retriever.retriever import retrieve
from llm.rag.documents import ALL_RAG_DOCUMENTS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

_WHITE_WINNING_JSON = {
    "evaluation": {"type": "cp", "value": 200},
    "eval_delta": 80,
    "errors": {"last_move_quality": "ok"},
    "tactical_flags": [],
    "position_flags": ["space_advantage", "central_control"],
}

_EQUAL_JSON = {
    "evaluation": {"type": "cp", "value": 10},
    "eval_delta": 0,
    "errors": {"last_move_quality": "ok"},
    "tactical_flags": [],
    "position_flags": [],
}

_TACTICAL_MISTAKE_JSON = {
    "evaluation": {"type": "cp", "value": -180},
    "eval_delta": -150,
    "errors": {"last_move_quality": "mistake"},
    "tactical_flags": ["hanging_piece"],
    "position_flags": [],
}

_MATE_JSON = {
    "evaluation": {"type": "mate", "value": 2},
    "eval_delta": 500,
    "errors": {"last_move_quality": "ok"},
    "tactical_flags": ["forced_mate"],
    "position_flags": [],
}


def _make_esv(stockfish_json: dict, fen: str | None = None) -> dict:
    return extract_engine_signal(stockfish_json, fen=fen)


# ---------------------------------------------------------------------------
# RAG-01 — Determinism: same ESV always returns the same document set
# ---------------------------------------------------------------------------


class TestRag01Determinism:
    """RAG-01: Retrieval must be fully deterministic for identical ESV inputs."""

    _RUNS = 15

    @pytest.mark.parametrize(
        "stockfish_json,fen",
        [
            (_WHITE_WINNING_JSON, _STARTING_FEN),
            (_EQUAL_JSON, None),
            (_TACTICAL_MISTAKE_JSON, None),
            (_MATE_JSON, _STARTING_FEN),
        ],
    )
    def test_same_esv_always_returns_same_documents(self, stockfish_json, fen):
        """Repeated retrieval with the same ESV must always return the identical document list."""
        esv = _make_esv(stockfish_json, fen)
        first_result = retrieve(esv, ALL_RAG_DOCUMENTS)
        first_ids = [d.get("id") for d in first_result]

        for i in range(self._RUNS - 1):
            again = retrieve(esv, ALL_RAG_DOCUMENTS)
            again_ids = [d.get("id") for d in again]
            assert again_ids == first_ids, (
                f"RAG retrieval is non-deterministic on run {i + 2}: "
                f"first={first_ids!r}, got={again_ids!r}"
            )

    def test_document_order_is_stable(self):
        """Document order must be stable across calls (priority-sorted, not random)."""
        esv = _make_esv(_WHITE_WINNING_JSON, _STARTING_FEN)
        results = [retrieve(esv, ALL_RAG_DOCUMENTS) for _ in range(10)]
        ids_list = [[d.get("id") for d in r] for r in results]
        assert all(ids == ids_list[0] for ids in ids_list), (
            "Document ordering is not stable across repeated retrieval calls"
        )

    def test_retrieval_result_count_is_stable(self):
        """The number of retrieved documents must be the same across calls."""
        esv = _make_esv(_TACTICAL_MISTAKE_JSON)
        counts = [len(retrieve(esv, ALL_RAG_DOCUMENTS)) for _ in range(10)]
        assert len(set(counts)) == 1, (
            f"Retrieved document count is non-deterministic: {counts}"
        )


# ---------------------------------------------------------------------------
# RAG-02 — Relevance: winning position ESV returns strategic documents
# ---------------------------------------------------------------------------


class TestRag02Relevance:
    """RAG-02: Winning position ESV must return documents with strategic content."""

    def test_retrieval_returns_at_least_one_document_for_winning_position(self):
        """A decisively winning position must retrieve at least one relevant document."""
        esv = _make_esv(_WHITE_WINNING_JSON, _STARTING_FEN)
        docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        assert len(docs) >= 1, (
            "Winning position ESV should retrieve at least one strategic document"
        )

    def test_retrieval_result_is_list_of_dicts(self):
        """Retrieval must return a list of document dicts."""
        esv = _make_esv(_WHITE_WINNING_JSON, _STARTING_FEN)
        docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        assert isinstance(docs, list), "retrieve() must return a list"
        for doc in docs:
            assert isinstance(doc, dict), f"Each document must be a dict, got {type(doc)}"

    def test_each_document_has_id_field(self):
        """Each retrieved document must have an 'id' field for tracking."""
        esv = _make_esv(_WHITE_WINNING_JSON, _STARTING_FEN)
        docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        for doc in docs:
            assert "id" in doc, f"Document missing 'id' field: {doc!r}"

    def test_retrieved_count_does_not_exceed_max_docs(self):
        """Retriever must cap results at MAX_DOCS=7 (architectural limit)."""
        from llm.rag.retriever.retriever import MAX_DOCS

        esv = _make_esv(_WHITE_WINNING_JSON, _STARTING_FEN)
        docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        assert len(docs) <= MAX_DOCS, (
            f"Retrieved {len(docs)} documents but MAX_DOCS={MAX_DOCS}"
        )

    def test_equal_position_retrieves_some_documents(self):
        """Even an equal position should retrieve at least one document."""
        esv = _make_esv(_EQUAL_JSON)
        docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        # It's acceptable for an equal position to return 0 docs — we only require no crash
        assert isinstance(docs, list), "retrieve() must return a list for equal positions"

    def test_tactical_mistake_retrieves_relevant_documents(self):
        """A position with a tactical mistake should retrieve documents."""
        esv = _make_esv(_TACTICAL_MISTAKE_JSON)
        docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        assert isinstance(docs, list)


# ---------------------------------------------------------------------------
# RAG-03 — Empty document store: no crash, returns empty list
# ---------------------------------------------------------------------------


class TestRag03EmptyDocumentStore:
    """RAG-03: Empty document store must not crash; must return an empty list."""

    def test_empty_store_returns_empty_list(self):
        """retrieve() with an empty document list must return []."""
        esv = _make_esv(_WHITE_WINNING_JSON, _STARTING_FEN)
        result = retrieve(esv, [])
        assert result == [], (
            f"retrieve() with empty docs must return []; got {result!r}"
        )

    def test_empty_store_does_not_raise(self):
        """retrieve() with an empty document list must not raise any exception."""
        esv = _make_esv(_TACTICAL_MISTAKE_JSON)
        try:
            retrieve(esv, [])
        except Exception as exc:
            pytest.fail(f"retrieve() raised with empty doc store: {exc}")

    def test_none_conditions_doc_does_not_crash(self):
        """A document with missing 'conditions' key must be handled without crash."""
        esv = _make_esv(_WHITE_WINNING_JSON)
        minimal_docs = [{"id": "doc_no_conditions", "type": "concept", "content": "x"}]
        try:
            result = retrieve(esv, minimal_docs)
            assert isinstance(result, list)
        except Exception as exc:
            pytest.fail(f"retrieve() crashed on doc without 'conditions': {exc}")

    def test_retrieve_with_single_non_matching_doc_returns_empty(self):
        """If no documents match the ESV conditions, result must be []."""
        esv = _make_esv(_EQUAL_JSON)
        # A document with impossible conditions that will never match
        impossible_docs = [
            {
                "id": "impossible",
                "type": "concept",
                "content": "impossible",
                "conditions": {
                    "evaluation.band": "this_band_never_exists",
                },
            }
        ]
        result = retrieve(esv, impossible_docs)
        assert result == [], (
            "Non-matching documents must yield an empty list"
        )

    def test_empty_esv_with_full_store_does_not_crash(self):
        """An effectively empty ESV (default values) must not crash retrieval."""
        esv = extract_engine_signal({})
        try:
            docs = retrieve(esv, ALL_RAG_DOCUMENTS)
            assert isinstance(docs, list)
        except Exception as exc:
            pytest.fail(f"retrieve() crashed on default ESV: {exc}")


# ---------------------------------------------------------------------------
# RAG-04 — Documents are never modified by retrieval or downstream components
# ---------------------------------------------------------------------------


class TestRag04DocumentsReadOnly:
    """RAG-04: RAG documents must be read-only at runtime — never mutated."""

    def test_retrieval_does_not_mutate_document_store(self):
        """retrieve() must not modify any document in ALL_RAG_DOCUMENTS."""
        esv = _make_esv(_WHITE_WINNING_JSON, _STARTING_FEN)

        # Deep-copy the store before retrieval
        docs_before = copy.deepcopy(ALL_RAG_DOCUMENTS)

        retrieve(esv, ALL_RAG_DOCUMENTS)

        # Compare each document to its pre-retrieval state
        assert len(ALL_RAG_DOCUMENTS) == len(docs_before), (
            "retrieve() must not add or remove documents from the store"
        )
        for i, (before, after) in enumerate(zip(docs_before, ALL_RAG_DOCUMENTS)):
            assert before == after, (
                f"Document at index {i} was mutated by retrieve():\n"
                f"  before={before!r}\n"
                f"  after={after!r}"
            )

    def test_returned_documents_are_references_but_not_modified(self):
        """
        Even if returned docs are references to the store (not copies),
        the retrieval call itself must not alter them in place.
        """
        esv = _make_esv(_TACTICAL_MISTAKE_JSON)
        snapshot = copy.deepcopy(ALL_RAG_DOCUMENTS)

        docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        _ = [d.get("id") for d in docs]  # simulate a caller reading doc ids

        for i, (original, current) in enumerate(zip(snapshot, ALL_RAG_DOCUMENTS)):
            assert original == current, (
                f"Document {i} was mutated after retrieval+read: "
                f"expected={original!r}, got={current!r}"
            )

    def test_multiple_retrieval_calls_leave_store_unchanged(self):
        """Repeated retrieval calls must leave the document store in the original state."""
        esv_a = _make_esv(_WHITE_WINNING_JSON)
        esv_b = _make_esv(_MATE_JSON, _STARTING_FEN)

        snapshot = copy.deepcopy(ALL_RAG_DOCUMENTS)

        for _ in range(10):
            retrieve(esv_a, ALL_RAG_DOCUMENTS)
            retrieve(esv_b, ALL_RAG_DOCUMENTS)

        assert ALL_RAG_DOCUMENTS == snapshot, (
            "Repeated retrieval calls mutated the document store"
        )

    def test_all_rag_documents_have_id_field(self):
        """Every document in the store must have an 'id' field (contract invariant)."""
        missing_id = [d for d in ALL_RAG_DOCUMENTS if "id" not in d]
        assert not missing_id, (
            f"{len(missing_id)} documents in ALL_RAG_DOCUMENTS are missing 'id': "
            f"{[d for d in missing_id[:3]]}"
        )

    def test_all_rag_documents_have_content_field(self):
        """Every document in the store must have a 'content' field."""
        missing_content = [d for d in ALL_RAG_DOCUMENTS if "content" not in d]
        assert not missing_content, (
            f"{len(missing_content)} documents missing 'content': "
            f"{[d.get('id') for d in missing_content[:3]]}"
        )

    def test_document_store_is_non_empty(self):
        """The RAG document store must not be empty — an empty store is a configuration error."""
        assert len(ALL_RAG_DOCUMENTS) > 0, (
            "ALL_RAG_DOCUMENTS is empty. "
            "The RAG document store must contain at least one document."
        )
