"""
``call_llm`` — DeepSeek streaming wire-format unit tests.

Tests the ``call_llm`` function in
``llm/seca/coach/explain_pipeline.py`` directly, mocking
``httpx.stream``.  Covers the SSE-consumption branches added in
PR 8 (2026-05-14) when ``call_llm`` switched from
``stream: False`` to ``stream: True``:

  CALL_LLM_01  Missing API key raises LLMConfigError before any HTTP call
  CALL_LLM_02  Happy path: SSE chunks concatenate into stripped full text
  CALL_LLM_03  Empty stream (no delta.content frames) raises httpx.HTTPError
  CALL_LLM_04  Malformed mid-stream chunks are skipped, stream continues
  CALL_LLM_05  HTTP 4xx propagates httpx.HTTPStatusError via raise_for_status
  CALL_LLM_06  ``[DONE]`` sentinel terminates the stream cleanly
  CALL_LLM_07  Non-data SSE lines (keep-alive comments, blank lines) are skipped
  CALL_LLM_09  ``max_completion_tokens`` maps to ``max_tokens`` in the body;
               the default omits the key (unchanged wire shape)

These complement ``test_explain_pipeline_retry.py`` which mocks
``call_llm`` at the chat-pipeline boundary; the tests here exercise
the streaming consumption path itself.
"""

from __future__ import annotations

from typing import Iterable
from unittest.mock import MagicMock, patch

import httpx
import pytest

from llm.seca.coach.explain_pipeline import LLMConfigError, call_llm


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _sse_event(content: str) -> str:
    """Build one SSE ``data: {…}`` line carrying a delta.content chunk."""
    import json

    payload = {"choices": [{"delta": {"content": content}}]}
    return f"data: {json.dumps(payload)}"


def _streaming_response_mock(
    sse_lines: Iterable[str],
    status_code: int = 200,
) -> MagicMock:
    """Build a ``httpx.stream`` substitute returning the given SSE lines.

    The real ``httpx.stream`` returns a context manager whose
    ``__enter__`` yields a ``Response`` with ``raise_for_status()``
    and ``iter_lines()``.  This mock mirrors that contract.
    """
    response_mock = MagicMock()

    if status_code >= 400:
        request = httpx.Request(
            "POST", "https://api.deepseek-test.com/chat/completions"
        )
        synthetic_response = httpx.Response(status_code=status_code, request=request)
        response_mock.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=request, response=synthetic_response
        )
    else:
        response_mock.raise_for_status = MagicMock()  # no-op

    response_mock.iter_lines.return_value = iter(list(sse_lines))

    context = MagicMock()
    context.__enter__ = MagicMock(return_value=response_mock)
    context.__exit__ = MagicMock(return_value=False)
    return context


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCallLLM:

    def test_call_llm_01_missing_api_key_raises_config_error(self, monkeypatch):
        """CALL_LLM_01: With COACH_DEEPSEEK_API_KEY unset, call_llm raises
        LLMConfigError BEFORE attempting any HTTP call.  Surfaces the
        misconfig immediately rather than silently degrading."""
        monkeypatch.delenv("COACH_DEEPSEEK_API_KEY", raising=False)

        with patch("llm.seca.coach.explain_pipeline.httpx.stream") as mock_stream:
            mock_stream.side_effect = AssertionError(
                "call_llm must not open a stream when API key is unset"
            )
            with pytest.raises(LLMConfigError, match="COACH_DEEPSEEK_API_KEY"):
                call_llm("ignored")

        mock_stream.assert_not_called()

    def test_call_llm_02_happy_path_concatenates_sse_chunks(self, monkeypatch):
        """CALL_LLM_02: a successful SSE stream of delta.content frames
        is concatenated and stripped to a single return string."""
        monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")

        sse_lines = [
            _sse_event("  Hello"),
            _sse_event(", "),
            _sse_event("world."),
            _sse_event("  "),  # trailing whitespace becomes part of buffer
            "data: [DONE]",
        ]
        with patch(
            "llm.seca.coach.explain_pipeline.httpx.stream",
            return_value=_streaming_response_mock(sse_lines),
        ):
            assert call_llm("ignored") == "Hello, world."

    def test_call_llm_03_empty_stream_raises_http_error(self, monkeypatch):
        """CALL_LLM_03: a stream that closes without delivering any
        non-empty delta.content surfaces as httpx.HTTPError so the
        upstream retry / fallback path treats it as a transport failure."""
        monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")

        sse_lines = [
            "data: [DONE]",
        ]
        with patch(
            "llm.seca.coach.explain_pipeline.httpx.stream",
            return_value=_streaming_response_mock(sse_lines),
        ):
            with pytest.raises(httpx.HTTPError, match="no content"):
                call_llm("ignored")

    def test_call_llm_04_malformed_chunk_is_skipped(self, monkeypatch):
        """CALL_LLM_04: a malformed JSON frame in the middle of the
        stream is skipped — the call returns the content from the
        surrounding valid frames.  This is the resilience knob for
        keep-alive comments, partial frames at network boundaries, and
        provider-side hiccups that occasionally interleave the stream."""
        monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")

        sse_lines = [
            _sse_event("Hello"),
            "data: not-valid-json{",  # malformed
            _sse_event(", world."),
            "data: [DONE]",
        ]
        with patch(
            "llm.seca.coach.explain_pipeline.httpx.stream",
            return_value=_streaming_response_mock(sse_lines),
        ):
            assert call_llm("ignored") == "Hello, world."

    def test_call_llm_05_http_4xx_propagates(self, monkeypatch):
        """CALL_LLM_05: HTTP 4xx (auth failed, billing exhausted, etc.)
        propagates as httpx.HTTPStatusError via response.raise_for_status()
        before any chunks are consumed.  chat_pipeline catches this with
        ``except Exception`` and falls back to the deterministic template."""
        monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")

        with patch(
            "llm.seca.coach.explain_pipeline.httpx.stream",
            return_value=_streaming_response_mock([], status_code=401),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                call_llm("ignored")

    def test_call_llm_06_done_sentinel_terminates(self, monkeypatch):
        """CALL_LLM_06: the ``data: [DONE]`` sentinel terminates the
        stream — frames after it are not consumed.  Mirrors OpenAI's
        SSE end-of-stream convention."""
        monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")

        sse_lines = [
            _sse_event("Done."),
            "data: [DONE]",
            _sse_event(" Should be ignored"),  # post-DONE, must not appear
        ]
        with patch(
            "llm.seca.coach.explain_pipeline.httpx.stream",
            return_value=_streaming_response_mock(sse_lines),
        ):
            assert call_llm("ignored") == "Done."

    def test_call_llm_07_non_data_lines_skipped(self, monkeypatch):
        """CALL_LLM_07: blank lines, SSE event-name lines, and comment
        lines (``:`` prefix for keep-alives) are all skipped without
        affecting the assembled output."""
        monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")

        sse_lines = [
            "",  # blank
            ": keep-alive comment",  # not "data:" prefix
            "event: message",  # SSE event name, not data
            _sse_event("Hello."),
            "",
            "data: [DONE]",
        ]
        with patch(
            "llm.seca.coach.explain_pipeline.httpx.stream",
            return_value=_streaming_response_mock(sse_lines),
        ):
            assert call_llm("ignored") == "Hello."

    def test_call_llm_09_max_tokens_sent_only_when_capped(self, monkeypatch):
        """CALL_LLM_09: ``max_completion_tokens`` lands as ``max_tokens``
        in the request body; the default (None) omits the key so the
        BaseLLM adapter / smoke-test wire shape is unchanged."""
        monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")

        sse_lines = [_sse_event("Hi."), "data: [DONE]"]
        with patch(
            "llm.seca.coach.explain_pipeline.httpx.stream",
            return_value=_streaming_response_mock(sse_lines),
        ) as mock_stream:
            call_llm("ignored")
        assert "max_tokens" not in mock_stream.call_args.kwargs["json"], (
            "uncapped calls must not send max_tokens"
        )

        with patch(
            "llm.seca.coach.explain_pipeline.httpx.stream",
            return_value=_streaming_response_mock(list(sse_lines)),
        ) as mock_stream:
            call_llm("ignored", max_completion_tokens=500)
        assert mock_stream.call_args.kwargs["json"]["max_tokens"] == 500

    def test_call_llm_08_oversized_response_truncates(self, monkeypatch):
        """CALL_LLM_08: a streaming response that exceeds the
        ``_MAX_STREAM_RESPONSE_BYTES`` cap stops consumption early
        rather than OOM-ing the container.  Defense-in-depth against
        a compromised provider streaming tokens at near-line-rate for
        the full 120s timeout.  Downstream validators handle the
        truncated text on their normal contract path.
        """
        from llm.seca.coach.explain_pipeline import _MAX_STREAM_RESPONSE_BYTES

        monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")

        # Each chunk is 1 kB.  Send enough to clearly exceed the cap.
        chunk_size_kb = 1
        oversize_chunks_needed = (_MAX_STREAM_RESPONSE_BYTES // (chunk_size_kb * 1024)) + 50
        big_chunk = "x" * (chunk_size_kb * 1024)
        sse_lines = [_sse_event(big_chunk) for _ in range(oversize_chunks_needed)]
        sse_lines.append("data: [DONE]")  # would arrive after the cap fires

        with patch(
            "llm.seca.coach.explain_pipeline.httpx.stream",
            return_value=_streaming_response_mock(sse_lines),
        ):
            result = call_llm("ignored")

        # Truncated at the cap (plus the in-flight chunk that crossed
        # the boundary; the cap is a >= test, not a hard ceiling).
        assert _MAX_STREAM_RESPONSE_BYTES <= len(result) <= (
            _MAX_STREAM_RESPONSE_BYTES + chunk_size_kb * 1024
        ), f"truncated length {len(result)} outside expected window"
