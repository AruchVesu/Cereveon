"""
``call_llm`` — DeepSeek wire-format unit tests.

Tests the ``call_llm`` function in ``llm/explain_pipeline.py`` directly,
mocking ``httpx.post``.  Covers the new branches added in the
Ollama → DeepSeek migration:

  CALL_LLM_01  Missing API key raises LLMConfigError before any HTTP call
  CALL_LLM_02  Happy path: response.choices[0].message.content extracted + stripped
  CALL_LLM_03  Malformed response (no choices key) raises httpx.HTTPError so
               the chat_pipeline fallback path catches it
  CALL_LLM_04  Malformed response (empty choices array) raises httpx.HTTPError
  CALL_LLM_05  HTTP 4xx propagates httpx.HTTPStatusError via raise_for_status

These complement test_explain_pipeline_retry.py which mocks call_llm at
the chat-pipeline boundary; the tests here exercise call_llm itself.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from llm.explain_pipeline import LLMConfigError, call_llm


def _fake_response(status_code: int, json_body: dict) -> httpx.Response:
    """Build an httpx.Response carrying a synthetic JSON body."""
    request = httpx.Request("POST", "https://api.deepseek-test.com/chat/completions")
    return httpx.Response(status_code=status_code, json=json_body, request=request)


class TestCallLLM:

    def test_call_llm_01_missing_api_key_raises_config_error(self, monkeypatch):
        """CALL_LLM_01: With COACH_DEEPSEEK_API_KEY unset, call_llm raises
        LLMConfigError BEFORE attempting any HTTP call.  This surfaces the
        misconfig immediately rather than silently degrading."""
        monkeypatch.delenv("COACH_DEEPSEEK_API_KEY", raising=False)

        # If httpx.post is somehow still called, fail loudly.
        with patch("llm.explain_pipeline.httpx.post") as mock_post:
            mock_post.side_effect = AssertionError("call_llm must not POST when API key is unset")
            with pytest.raises(LLMConfigError, match="COACH_DEEPSEEK_API_KEY"):
                call_llm("ignored")

        mock_post.assert_not_called()

    def test_call_llm_02_happy_path_extracts_content(self, monkeypatch):
        """CALL_LLM_02: 200 response with choices[0].message.content is
        extracted and stripped."""
        monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")

        with patch("llm.explain_pipeline.httpx.post") as mock_post:
            mock_post.return_value = _fake_response(
                200,
                {"choices": [{"message": {"content": "  Hello, world.  "}}]},
            )
            assert call_llm("ignored") == "Hello, world."

    def test_call_llm_03_missing_choices_raises_http_error(self, monkeypatch):
        """CALL_LLM_03: 200 response without `choices` raises httpx.HTTPError
        so the upstream retry / fallback path catches it.  Catches a
        provider regression where the wire format diverges from
        OpenAI-compatible without us noticing."""
        monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")

        with patch("llm.explain_pipeline.httpx.post") as mock_post:
            mock_post.return_value = _fake_response(200, {"unexpected": "shape"})
            with pytest.raises(httpx.HTTPError, match="missing expected"):
                call_llm("ignored")

    def test_call_llm_04_empty_choices_raises_http_error(self, monkeypatch):
        """CALL_LLM_04: 200 with `choices: []` raises httpx.HTTPError —
        same regression-guard as CALL_LLM_03 but for the empty-array case
        which would otherwise silently return None or raise IndexError."""
        monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")

        with patch("llm.explain_pipeline.httpx.post") as mock_post:
            mock_post.return_value = _fake_response(200, {"choices": []})
            with pytest.raises(httpx.HTTPError):
                call_llm("ignored")

    def test_call_llm_05_http_4xx_propagates(self, monkeypatch):
        """CALL_LLM_05: HTTP 4xx (auth failed, billing exhausted, etc.)
        propagates as httpx.HTTPStatusError via response.raise_for_status().
        chat_pipeline.py:557 catches this with `except Exception` and falls
        back to the deterministic template."""
        monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")

        with patch("llm.explain_pipeline.httpx.post") as mock_post:
            mock_post.return_value = _fake_response(
                401,
                {"error": {"message": "Invalid API key"}},
            )
            with pytest.raises(httpx.HTTPStatusError):
                call_llm("ignored")
