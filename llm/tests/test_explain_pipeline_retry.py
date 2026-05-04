"""
API05 — LLM retry path must be capped and must enforce a minimum delay between
attempts so the Ollama provider is not flooded with rapid-fire requests.

Three contracts:
  RETRY_CALL_CAP      call_llm is called at most MAX_RETRIES+1 times total.
  RETRY_BACKOFF_DELAY at least _RETRY_DELAY_SECONDS elapse between the first
                      and last call when all attempts fail validation.
  RETRY_FALLBACK_SAFE when all retries are exhausted a safe string is returned;
                      no exception propagates to the caller.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from llm.explain_pipeline import (
    MAX_RETRIES,
    _RETRY_DELAY_SECONDS,
    generate_validated_explanation,
)

_DUMMY_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_DUMMY_SF: dict = {}


class TestExplainPipelineRetryBehaviour:
    """API05 — retry cap, backoff delay, and safe fallback."""

    def test_retry_call_cap(self):
        """RETRY_CALL_CAP: call_llm is invoked at most MAX_RETRIES+1 times."""
        call_count = 0

        def _count_and_return(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return "dummy"

        with (
            patch("llm.explain_pipeline.call_llm", side_effect=_count_and_return),
            patch(
                "llm.explain_pipeline.validate_mode_2_negative",
                side_effect=AssertionError("violation"),
            ),
        ):
            generate_validated_explanation(
                fen=_DUMMY_FEN,
                stockfish_json=_DUMMY_SF,
                user_query="",
            )

        assert call_count <= MAX_RETRIES + 1, (
            f"call_llm was called {call_count} times; "
            f"must be capped at MAX_RETRIES+1={MAX_RETRIES + 1}."
        )

    def test_retry_backoff_delay_enforced(self):
        """RETRY_BACKOFF_DELAY: at least _RETRY_DELAY_SECONDS * MAX_RETRIES total
        delay must elapse across all retry attempts so the provider is not flooded."""
        call_times: list[float] = []

        def _record_time(prompt: str) -> str:
            call_times.append(time.monotonic())
            return "dummy"

        with (
            patch("llm.explain_pipeline.call_llm", side_effect=_record_time),
            patch(
                "llm.explain_pipeline.validate_mode_2_negative",
                side_effect=AssertionError("violation"),
            ),
        ):
            generate_validated_explanation(
                fen=_DUMMY_FEN,
                stockfish_json=_DUMMY_SF,
                user_query="",
            )

        assert len(call_times) >= 2, "Need at least 2 call_llm invocations to measure delay."
        total_elapsed = call_times[-1] - call_times[0]
        min_expected = _RETRY_DELAY_SECONDS * MAX_RETRIES
        assert total_elapsed >= min_expected * 0.9, (
            f"Total elapsed across retries was {total_elapsed:.3f}s; "
            f"expected >= {min_expected:.1f}s ({MAX_RETRIES} x {_RETRY_DELAY_SECONDS}s). "
            "Add a sleep between retries in explain_pipeline.py."
        )

    def test_fallback_returned_after_all_retries_fail(self):
        """RETRY_FALLBACK_SAFE: safe string returned when all retries exhausted;
        no exception propagates to the caller."""
        with (
            patch("llm.explain_pipeline.call_llm", return_value="dummy"),
            patch(
                "llm.explain_pipeline.validate_mode_2_negative",
                side_effect=AssertionError("violation"),
            ),
        ):
            result, esv = generate_validated_explanation(
                fen=_DUMMY_FEN,
                stockfish_json=_DUMMY_SF,
                user_query="",
            )

        assert isinstance(result, str) and len(result) > 0
        assert "cannot provide" in result, f"Unexpected fallback text: {result!r}"
        assert isinstance(esv, dict)
