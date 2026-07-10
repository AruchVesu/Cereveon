"""Tests for the LLM observability surface added alongside the
hardware/LLM monitoring dashboard.

Pinned invariants
-----------------
LLM_MET_01  Cost calculation matches the published DeepSeek price table.
LLM_MET_02  Unknown model returns 0.0 cost and never raises.
LLM_MET_03  ``observe_llm_call`` records latency on the histogram for
            every outcome (ok, timeout, http_error, parse_error, empty).
LLM_MET_04  ``observe_llm_call(outcome="ok")`` increments token + cost
            counters with the expected per-kind splits.
LLM_MET_05  Non-ok outcomes increment ``llm_errors_total`` and do NOT
            increment tokens / cost.
LLM_MET_06  ``call_llm`` extracts ``usage`` from the final SSE chunk and
            forwards it to the metric counters.
LLM_MET_07  ``call_llm`` records outcome=``timeout`` on ``httpx.TimeoutException``.
LLM_MET_08  ``call_llm`` records outcome=``http_error`` with category
            ``http_5xx`` on a 5xx response.
LLM_MET_09  ``call_llm`` records outcome=``empty`` when the stream
            yields no content delta.
LLM_MET_10  The new metric names are exposed on the registry.
LLM_MET_11  Cache-split-aware cost: a hit/miss split that accounts for
            prompt_tokens prices hits at the cache-hit rate; a split
            that doesn't add up falls back to flat miss pricing.
LLM_MET_12  ``observe_llm_call(outcome="ok")`` with a cache split lands
            the split on ``llm_prompt_cache_tokens_total`` and prices
            the prompt cost split-aware.
LLM_MET_13  ``observe_llm_call(outcome="aborted")`` records tokens AND
            increments the error counter — an aborted stream is billed
            spend that must not read zero (unlike transport failures,
            which have no usage and record errors only, LLM_MET_05).
LLM_MET_14  A consumer abandoning ``call_llm_stream`` mid-stream records
            outcome=aborted with ESTIMATED token usage.
LLM_MET_15  ``call_llm`` extracts the DeepSeek prompt-cache split from
            the usage frame and forwards it to the cache counter.
"""

from __future__ import annotations

import json
from typing import Iterable
from unittest.mock import MagicMock, patch

import httpx
import pytest
from prometheus_client import REGISTRY


# ---------------------------------------------------------------------------
# SSE mock — mirrors the helper in test_call_llm_deepseek.py but adds an
# include_usage chunk so the call_llm-side tests can exercise the new
# usage-extraction path.
# ---------------------------------------------------------------------------


def _sse_content(content: str) -> str:
    return f"data: {json.dumps({'choices': [{'delta': {'content': content}}]})}"


def _sse_usage(
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int | None = None,
    cache_miss_tokens: int | None = None,
) -> str:
    usage: dict = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if cache_hit_tokens is not None:
        usage["prompt_cache_hit_tokens"] = cache_hit_tokens
    if cache_miss_tokens is not None:
        usage["prompt_cache_miss_tokens"] = cache_miss_tokens
    payload = {"choices": [], "usage": usage}
    return f"data: {json.dumps(payload)}"


def _stream_mock(sse_lines: Iterable[str], status_code: int = 200) -> MagicMock:
    response = MagicMock()
    if status_code >= 400:
        request = httpx.Request("POST", "https://api.deepseek-test/x")
        synthetic = httpx.Response(status_code=status_code, request=request)
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=request, response=synthetic
        )
    else:
        response.raise_for_status = MagicMock()
    response.iter_lines.return_value = iter(list(sse_lines))
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=response)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _counter_value(metric, **labels) -> float:
    """Read a Counter sample; missing samples count as 0 so tests can
    assert on deltas without pre-existence checks."""
    try:
        return float(metric.labels(**labels)._value.get())  # noqa: SLF001
    except Exception:
        return 0.0


def _hist_count(metric, **labels) -> float:
    """Return the ``_count`` of a Histogram for the given label set."""
    sample_value = REGISTRY.get_sample_value(
        metric._name + "_count", labels  # noqa: SLF001
    )
    return float(sample_value or 0.0)


# ---------------------------------------------------------------------------
# Direct unit tests on observability.cost_for_call / observe_llm_call
# ---------------------------------------------------------------------------


def test_llm_met_01_cost_matches_price_table():
    """LLM_MET_01: deepseek-chat is $0.00014/1k prompt + $0.00028/1k completion."""
    from llm import observability

    cost = observability.cost_for_call("deepseek-chat", 1000, 500)
    expected = 0.00014 * 1 + 0.00028 * 0.5
    assert cost == pytest.approx(expected, rel=1e-6)


def test_llm_met_01b_v4_flash_priced_same_as_deepseek_chat():
    """LLM_MET_01b: deepseek-v4-flash must have a price row.

    The legacy name deepseek-chat is deprecated 2026-07-24 in favour of
    deepseek-v4-flash (same engine, non-thinking mode).  Without this
    row, bumping COACH_DEEPSEEK_MODEL would silently zero the cost
    metric (unknown models cost 0.0 by design — LLM_MET_02).  Priced
    identically to deepseek-chat today; when DeepSeek's announced
    mid-July 2026 increase (~2x) lands, update BOTH rows and BOTH
    pins together.
    """
    from llm import observability

    assert observability.cost_for_call(
        "deepseek-v4-flash", 1000, 500
    ) == pytest.approx(observability.cost_for_call("deepseek-chat", 1000, 500), rel=1e-9)
    assert observability.cost_for_call("deepseek-v4-flash", 1000, 500) > 0.0


def test_llm_met_02_unknown_model_returns_zero_and_does_not_raise():
    """LLM_MET_02: An unknown model logs a warning but never raises.
    Cost is an observability signal, not a control signal."""
    from llm import observability

    # First call triggers the warning; subsequent calls are silent.
    observability._COST_WARNED_MODELS.discard("never-heard-of-this")
    cost = observability.cost_for_call("never-heard-of-this", 1000, 500)
    assert cost == 0.0
    # Second call must also not raise.
    cost_again = observability.cost_for_call("never-heard-of-this", 1, 1)
    assert cost_again == 0.0


def test_llm_met_03_histogram_observes_every_outcome():
    """LLM_MET_03: each outcome label observes on the latency histogram."""
    from llm import observability

    before = {
        outcome: _hist_count(
            observability.llm_request_duration_seconds,
            model="test-model",
            outcome=outcome,
        )
        for outcome in ("ok", "timeout", "http_error", "parse_error", "empty")
    }

    for outcome in before:
        observability.observe_llm_call(
            model="test-model",
            outcome=outcome,
            duration_seconds=0.123,
            error_category=outcome if outcome != "ok" else None,
        )

    for outcome, prev in before.items():
        after = _hist_count(
            observability.llm_request_duration_seconds,
            model="test-model",
            outcome=outcome,
        )
        assert after == prev + 1, f"{outcome} histogram did not increment"


def test_llm_met_04_ok_outcome_records_tokens_and_cost():
    """LLM_MET_04: outcome=ok with non-zero tokens increments token + cost
    counters split by kind."""
    from llm import observability

    model = "deepseek-chat"
    before_prompt_tok = _counter_value(
        observability.llm_tokens_total, model=model, kind="prompt"
    )
    before_compl_tok = _counter_value(
        observability.llm_tokens_total, model=model, kind="completion"
    )
    before_prompt_cost = _counter_value(
        observability.llm_cost_usd_total, model=model, kind="prompt"
    )

    observability.observe_llm_call(
        model=model,
        outcome="ok",
        duration_seconds=0.5,
        prompt_tokens=2000,
        completion_tokens=500,
    )

    after_prompt_tok = _counter_value(
        observability.llm_tokens_total, model=model, kind="prompt"
    )
    after_compl_tok = _counter_value(
        observability.llm_tokens_total, model=model, kind="completion"
    )
    after_prompt_cost = _counter_value(
        observability.llm_cost_usd_total, model=model, kind="prompt"
    )

    assert after_prompt_tok == before_prompt_tok + 2000
    assert after_compl_tok == before_compl_tok + 500
    # 2000 / 1000 * 0.00014 = 0.00028
    assert after_prompt_cost == pytest.approx(before_prompt_cost + 0.00028, rel=1e-6)


def test_llm_met_05_non_ok_increments_error_counter_only():
    """LLM_MET_05: error outcomes increment llm_errors_total but NOT
    the token / cost counters."""
    from llm import observability

    model = "deepseek-chat"
    before_err = _counter_value(
        observability.llm_errors_total, model=model, category="timeout"
    )
    before_tok = _counter_value(
        observability.llm_tokens_total, model=model, kind="prompt"
    )

    observability.observe_llm_call(
        model=model,
        outcome="timeout",
        duration_seconds=120.0,
        prompt_tokens=999,  # MUST be ignored on non-ok outcomes
        completion_tokens=999,
        error_category="timeout",
    )

    after_err = _counter_value(
        observability.llm_errors_total, model=model, category="timeout"
    )
    after_tok = _counter_value(
        observability.llm_tokens_total, model=model, kind="prompt"
    )

    assert after_err == before_err + 1
    assert after_tok == before_tok, "tokens must not increment on non-ok outcomes"


# ---------------------------------------------------------------------------
# call_llm-side instrumentation
# ---------------------------------------------------------------------------


def test_llm_met_06_call_llm_extracts_usage_from_final_chunk(monkeypatch):
    """LLM_MET_06: call_llm reads ``usage`` from the final SSE chunk and
    forwards it to the metric counters."""
    monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")
    from llm import observability
    from llm.seca.coach.explain_pipeline import MODEL_NAME, call_llm

    before_tok = _counter_value(
        observability.llm_tokens_total, model=MODEL_NAME, kind="prompt"
    )

    sse_lines = [
        _sse_content("Hello"),
        _sse_content(" world"),
        _sse_usage(prompt_tokens=42, completion_tokens=7),
        "data: [DONE]",
    ]
    with patch(
        "llm.seca.coach.explain_pipeline.httpx.stream",
        return_value=_stream_mock(sse_lines),
    ):
        assert call_llm("ignored") == "Hello world"

    after_tok = _counter_value(
        observability.llm_tokens_total, model=MODEL_NAME, kind="prompt"
    )
    assert after_tok == before_tok + 42


def test_llm_met_07_call_llm_records_timeout(monkeypatch):
    """LLM_MET_07: httpx.TimeoutException → outcome=timeout, category=timeout."""
    monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")
    from llm import observability
    from llm.seca.coach.explain_pipeline import MODEL_NAME, call_llm

    before = _counter_value(
        observability.llm_errors_total, model=MODEL_NAME, category="timeout"
    )

    with patch(
        "llm.seca.coach.explain_pipeline.httpx.stream",
        side_effect=httpx.ReadTimeout("simulated timeout"),
    ):
        with pytest.raises(httpx.TimeoutException):
            call_llm("ignored")

    after = _counter_value(
        observability.llm_errors_total, model=MODEL_NAME, category="timeout"
    )
    assert after == before + 1


def test_llm_met_08_call_llm_records_http_5xx(monkeypatch):
    """LLM_MET_08: 5xx response → outcome=http_error, category=http_5xx."""
    monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")
    from llm import observability
    from llm.seca.coach.explain_pipeline import MODEL_NAME, call_llm

    before = _counter_value(
        observability.llm_errors_total, model=MODEL_NAME, category="http_5xx"
    )

    with patch(
        "llm.seca.coach.explain_pipeline.httpx.stream",
        return_value=_stream_mock([], status_code=503),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            call_llm("ignored")

    after = _counter_value(
        observability.llm_errors_total, model=MODEL_NAME, category="http_5xx"
    )
    assert after == before + 1


def test_llm_met_09_call_llm_records_empty_outcome(monkeypatch):
    """LLM_MET_09: a stream that closes without any delta.content
    surfaces as httpx.HTTPError AND records outcome=empty."""
    monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")
    from llm import observability
    from llm.seca.coach.explain_pipeline import MODEL_NAME, call_llm

    before = _counter_value(
        observability.llm_errors_total, model=MODEL_NAME, category="empty"
    )

    # Stream that goes straight to [DONE] without any content chunk.
    with patch(
        "llm.seca.coach.explain_pipeline.httpx.stream",
        return_value=_stream_mock(["data: [DONE]"]),
    ):
        with pytest.raises(httpx.HTTPError):
            call_llm("ignored")

    after = _counter_value(
        observability.llm_errors_total, model=MODEL_NAME, category="empty"
    )
    assert after == before + 1


def test_llm_met_10_metric_names_registered():
    """LLM_MET_10: the new metric names are present on the global registry
    (catches accidental rename / deletion)."""
    from prometheus_client.exposition import generate_latest

    body = generate_latest(REGISTRY).decode()
    expected = [
        "chesscoach_llm_request_duration_seconds",
        "chesscoach_llm_tokens_total",
        "chesscoach_llm_prompt_cache_tokens_total",
        "chesscoach_llm_cost_usd_total",
        "chesscoach_llm_errors_total",
    ]
    for name in expected:
        assert name in body, f"missing metric {name!r}"


# ---------------------------------------------------------------------------
# Prompt-cache split + aborted-stream accounting (2026-07-10)
# ---------------------------------------------------------------------------


def test_llm_met_11_cache_split_prices_hits_at_hit_rate():
    """LLM_MET_11: a split that accounts for prompt_tokens prices the hit
    portion at the cache-hit rate; a non-adding split falls back to flat
    miss pricing (identical to passing no split at all)."""
    from llm import observability

    # 900 hit + 100 miss on deepseek-chat:
    # 0.9 * 0.0000028 + 0.1 * 0.00014 = 0.00000252 + 0.000014
    split_cost = observability.cost_for_call(
        "deepseek-chat", 1000, 0, cache_hit_tokens=900, cache_miss_tokens=100
    )
    assert split_cost == pytest.approx(0.00000252 + 0.000014, rel=1e-6)
    # The same 1000 prompt tokens all-miss must cost the flat rate…
    flat_cost = observability.cost_for_call("deepseek-chat", 1000, 0)
    assert flat_cost == pytest.approx(0.00014, rel=1e-6)
    # …and a real split is strictly cheaper than flat (the whole point).
    assert split_cost < flat_cost
    # A split that does NOT add up to prompt_tokens is ignored (conservative).
    bogus_cost = observability.cost_for_call(
        "deepseek-chat", 1000, 0, cache_hit_tokens=500, cache_miss_tokens=100
    )
    assert bogus_cost == pytest.approx(flat_cost, rel=1e-9)


def test_llm_met_12_ok_with_split_lands_on_cache_counter():
    """LLM_MET_12: outcome=ok with a valid split increments the hit/miss
    cache-token series and prices the prompt cost split-aware."""
    from llm import observability

    model = "deepseek-chat"
    before_hit = _counter_value(
        observability.llm_prompt_cache_tokens_total, model=model, result="hit"
    )
    before_miss = _counter_value(
        observability.llm_prompt_cache_tokens_total, model=model, result="miss"
    )
    before_cost = _counter_value(
        observability.llm_cost_usd_total, model=model, kind="prompt"
    )

    observability.observe_llm_call(
        model=model,
        outcome="ok",
        duration_seconds=0.4,
        prompt_tokens=3000,
        completion_tokens=100,
        cache_hit_tokens=2000,
        cache_miss_tokens=1000,
    )

    assert _counter_value(
        observability.llm_prompt_cache_tokens_total, model=model, result="hit"
    ) == before_hit + 2000
    assert _counter_value(
        observability.llm_prompt_cache_tokens_total, model=model, result="miss"
    ) == before_miss + 1000
    # 2.0k * 0.0000028 + 1.0k * 0.00014
    expected_prompt_cost = 2 * 0.0000028 + 1 * 0.00014
    assert _counter_value(
        observability.llm_cost_usd_total, model=model, kind="prompt"
    ) == pytest.approx(before_cost + expected_prompt_cost, rel=1e-6)


def test_llm_met_13_aborted_records_tokens_and_error():
    """LLM_MET_13: outcome=aborted (consumer abandoned a healthy stream)
    records tokens — that spend was billed — AND counts as a failed
    attempt on the error counter."""
    from llm import observability

    model = "deepseek-chat"
    before_tok = _counter_value(
        observability.llm_tokens_total, model=model, kind="prompt"
    )
    before_err = _counter_value(
        observability.llm_errors_total, model=model, category="aborted"
    )

    observability.observe_llm_call(
        model=model,
        outcome="aborted",
        duration_seconds=1.2,
        prompt_tokens=1500,
        completion_tokens=30,
        error_category="aborted",
    )

    assert _counter_value(
        observability.llm_tokens_total, model=model, kind="prompt"
    ) == before_tok + 1500
    assert _counter_value(
        observability.llm_errors_total, model=model, category="aborted"
    ) == before_err + 1


def test_llm_met_14_abandoned_stream_records_estimated_usage(monkeypatch):
    """LLM_MET_14: closing the call_llm_stream generator mid-stream (the
    streaming pipeline's FORBID-rejection path) records outcome=aborted
    with tokens ESTIMATED from the prompt / consumed chars — the spend
    that used to read zero."""
    monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")
    from llm import observability
    from llm.seca.coach.explain_pipeline import (
        MODEL_NAME,
        _estimate_tokens,
        call_llm_stream,
    )

    prompt = "cache-accounting probe prompt, long enough to estimate from"
    before_tok = _counter_value(
        observability.llm_tokens_total, model=MODEL_NAME, kind="prompt"
    )
    before_err = _counter_value(
        observability.llm_errors_total, model=MODEL_NAME, category="aborted"
    )

    sse_lines = [
        _sse_content("Hello"),
        _sse_content(" world"),
        _sse_usage(prompt_tokens=42, completion_tokens=7),
        "data: [DONE]",
    ]
    with patch(
        "llm.seca.coach.explain_pipeline.httpx.stream",
        return_value=_stream_mock(sse_lines),
    ):
        gen = call_llm_stream(prompt)
        assert next(gen) == "Hello"
        gen.close()  # consumer rejects mid-stream — usage frame never read

    assert _counter_value(
        observability.llm_errors_total, model=MODEL_NAME, category="aborted"
    ) == before_err + 1
    # Prompt spend estimated from the prompt string, not read as zero.
    assert _counter_value(
        observability.llm_tokens_total, model=MODEL_NAME, kind="prompt"
    ) == before_tok + _estimate_tokens(len(prompt))


def test_llm_met_15_call_llm_forwards_cache_split(monkeypatch):
    """LLM_MET_15: the DeepSeek usage frame's prompt_cache_hit_tokens /
    prompt_cache_miss_tokens reach the cache counter."""
    monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")
    from llm import observability
    from llm.seca.coach.explain_pipeline import MODEL_NAME, call_llm

    before_hit = _counter_value(
        observability.llm_prompt_cache_tokens_total, model=MODEL_NAME, result="hit"
    )
    before_miss = _counter_value(
        observability.llm_prompt_cache_tokens_total, model=MODEL_NAME, result="miss"
    )

    sse_lines = [
        _sse_content("Hi"),
        _sse_usage(
            prompt_tokens=100,
            completion_tokens=3,
            cache_hit_tokens=64,
            cache_miss_tokens=36,
        ),
        "data: [DONE]",
    ]
    with patch(
        "llm.seca.coach.explain_pipeline.httpx.stream",
        return_value=_stream_mock(sse_lines),
    ):
        assert call_llm("ignored") == "Hi"

    assert _counter_value(
        observability.llm_prompt_cache_tokens_total, model=MODEL_NAME, result="hit"
    ) == before_hit + 64
    assert _counter_value(
        observability.llm_prompt_cache_tokens_total, model=MODEL_NAME, result="miss"
    ) == before_miss + 36
