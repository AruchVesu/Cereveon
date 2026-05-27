"""Tests for the game_id contextvar (llm/log_config.py) that attributes
LLM telemetry to a match.

Pinned invariants
-----------------
GID_01  ``game_id_var`` defaults to None outside any handler scope.
GID_02  ``set_game_id`` + ``reset`` toggles cleanly.
GID_03  The JSON formatter emits the game_id key when set, omits it when
        not.
GID_04  Concurrent asyncio tasks see isolated contextvar bindings — a
        binding set in one task does NOT leak into a sibling task.
GID_05  ``call_llm``'s structured log line carries game_id when set.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import MagicMock, patch

import httpx


# ---------------------------------------------------------------------------
# Direct tests on the contextvar
# ---------------------------------------------------------------------------


def test_gid_01_default_is_none():
    from llm import log_config

    assert log_config.game_id_var.get() is None


def test_gid_02_set_and_reset_round_trips():
    from llm import log_config

    token = log_config.set_game_id("game-42")
    try:
        assert log_config.game_id_var.get() == "game-42"
    finally:
        log_config.game_id_var.reset(token)
    assert log_config.game_id_var.get() is None


# ---------------------------------------------------------------------------
# Formatter integration
# ---------------------------------------------------------------------------


def _format_one(extra: dict | None = None) -> dict:
    from llm.log_config import JsonLogFormatter

    record = logging.LogRecord(
        name="llm.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return json.loads(JsonLogFormatter().format(record))


def test_gid_03_formatter_includes_game_id_when_set():
    from llm import log_config

    token = log_config.set_game_id("game-xyz")
    try:
        payload = _format_one()
        assert payload.get("game_id") == "game-xyz"
    finally:
        log_config.game_id_var.reset(token)

    # Once reset the field is omitted entirely (not null).
    payload = _format_one()
    assert "game_id" not in payload


# ---------------------------------------------------------------------------
# Asyncio isolation
# ---------------------------------------------------------------------------


def test_gid_04_concurrent_tasks_see_isolated_bindings():
    """Per-task contextvar isolation: a binding in one task must not
    leak into a sibling.  This is the property the route-handler
    try/finally relies on for correctness across overlapping requests."""
    from llm import log_config

    observed: dict[str, str | None] = {}

    async def _task(name: str, game_id: str | None) -> None:
        if game_id is not None:
            token = log_config.set_game_id(game_id)
        else:
            token = None
        try:
            await asyncio.sleep(0.01)
            observed[name] = log_config.game_id_var.get()
        finally:
            if token is not None:
                log_config.game_id_var.reset(token)

    async def _runner() -> None:
        await asyncio.gather(
            _task("a", "game-A"),
            _task("b", "game-B"),
            _task("c", None),
        )

    asyncio.run(_runner())
    assert observed == {"a": "game-A", "b": "game-B", "c": None}


# ---------------------------------------------------------------------------
# call_llm — log-line carries game_id
# ---------------------------------------------------------------------------


def _stream_mock_with_usage() -> MagicMock:
    """Minimal SSE mock — content + usage chunk + DONE."""
    sse_lines = [
        f"data: {json.dumps({'choices': [{'delta': {'content': 'ok'}}]})}",
        f"data: {json.dumps({'choices': [], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})}",
        "data: [DONE]",
    ]
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.iter_lines.return_value = iter(sse_lines)
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=response)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def test_gid_05_call_llm_log_line_carries_game_id(monkeypatch, caplog):
    """When call_llm runs inside a game_id scope, the structured llm_call
    log record carries the game_id; outside the scope it carries None."""
    monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-test")
    from llm import log_config
    from llm.seca.coach.explain_pipeline import call_llm

    # With game_id set ----------------------------------------------------
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    pipeline_logger = logging.getLogger("llm.seca.coach.explain_pipeline")
    handler = _Capture(level=logging.INFO)
    pipeline_logger.addHandler(handler)
    # Ensure INFO records propagate; some test harnesses raise root level.
    prev_level = pipeline_logger.level
    pipeline_logger.setLevel(logging.INFO)
    try:
        token = log_config.set_game_id("match-7")
        try:
            with patch(
                "llm.seca.coach.explain_pipeline.httpx.stream",
                return_value=_stream_mock_with_usage(),
            ):
                call_llm("ignored")
        finally:
            log_config.game_id_var.reset(token)
    finally:
        pipeline_logger.removeHandler(handler)
        pipeline_logger.setLevel(prev_level)

    llm_records = [r for r in captured if getattr(r, "event", None) == "llm_call"]
    assert llm_records, "call_llm did not emit an llm_call log record"
    assert getattr(llm_records[-1], "game_id", "missing") == "match-7"

    # Without game_id ----------------------------------------------------
    captured.clear()
    pipeline_logger.addHandler(handler)
    pipeline_logger.setLevel(logging.INFO)
    try:
        with patch(
            "llm.seca.coach.explain_pipeline.httpx.stream",
            return_value=_stream_mock_with_usage(),
        ):
            call_llm("ignored")
    finally:
        pipeline_logger.removeHandler(handler)
        pipeline_logger.setLevel(prev_level)

    llm_records = [r for r in captured if getattr(r, "event", None) == "llm_call"]
    assert llm_records, "call_llm did not emit an llm_call log record"
    assert getattr(llm_records[-1], "game_id", "still-missing") is None
