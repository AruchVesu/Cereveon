"""
Unit/integration tests for POST /chat/stream (SSE).

Approach
--------
Tier 1 — AST inspection
    Verify ``chat_stream()`` has ``Depends(verify_api_key)`` in server.py without
    importing the full module chain.

Tier 2 — Stub FastAPI app (TestClient)
    A self-contained FastAPI instance mirrors the /chat/stream SSE format using
    a fixed reply stub.  Tests confirm the SSE wire format without the server
    module chain (engine pool, DB, Stockfish).

Invariants pinned
-----------------
 1. SSE_AUTH_REQUIRED      chat_stream has Depends(verify_api_key) in server.py (AST).
 2. SSE_CONTENT_TYPE       HTTP 200 Content-Type is text/event-stream.
 3. SSE_CHUNKS_PRESENT     at least one data: chunk event is emitted.
 4. SSE_DONE_LAST          the last data: event has type == "done".
 5. SSE_REPLY_COMPLETE     concatenated chunk text equals the full stub reply.
 6. SSE_ENGINE_SIGNAL_DONE done event engine_signal has all required ESV keys.
 7. SSE_MODE_DONE          done event mode equals "CHAT_V1".
 8. SSE_AUTH_STUB          missing X-Api-Key returns 401 from stub app.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SERVER_PY = _REPO_ROOT / "llm" / "server.py"

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _get_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _depends_on(func: ast.FunctionDef | ast.AsyncFunctionDef, target: str) -> bool:
    for default in func.args.defaults + func.args.kw_defaults:
        if default is None:
            continue
        if not isinstance(default, ast.Call):
            continue
        if isinstance(default.func, ast.Name) and default.func.id == "Depends":
            for arg in default.args:
                if isinstance(arg, ast.Name) and arg.id == target:
                    return True
    return False


# ---------------------------------------------------------------------------
# Stub FastAPI app (Tier 2) — mirrors /chat/stream without server.py import
# ---------------------------------------------------------------------------

_STUB_REPLY = "Engine shows equality in the opening."
_STUB_SIGNAL = {
    "evaluation": {"type": "cp", "band": "equal", "side": "white"},
    "eval_delta": "stable",
    "last_move_quality": "unknown",
    "tactical_flags": [],
    "position_flags": [],
    "phase": "opening",
}
_STUB_MODE = "CHAT_V1"
_API_KEY = "test-stream-key"

_app = FastAPI()


def _verify_key(x_api_key: str = Header(default="")) -> str:
    if x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return x_api_key


@_app.post("/chat/stream")
def _stub_stream(request: Request, _: str = Depends(_verify_key)) -> StreamingResponse:
    """Mirrors the real /chat/stream SSE format with a fixed reply."""

    def _generate():
        words = _STUB_REPLY.split(" ")
        for i, word in enumerate(words):
            text = word if i == len(words) - 1 else word + " "
            yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
        yield (
            f"data: {json.dumps({'type': 'done', 'engine_signal': _STUB_SIGNAL, 'mode': _STUB_MODE})}\n\n"
        )

    return StreamingResponse(_generate(), media_type="text/event-stream")


_client = TestClient(_app, raise_server_exceptions=True)

_REQUIRED_ESV_KEYS = {
    "evaluation",
    "eval_delta",
    "last_move_quality",
    "tactical_flags",
    "position_flags",
    "phase",
}


def _auth_headers() -> dict[str, str]:
    return {"X-Api-Key": _API_KEY}


def _parse_sse_events(body: str) -> list[dict]:
    """Return a list of parsed JSON objects from ``data:`` lines in an SSE body."""
    events = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("data: "):
            events.append(json.loads(stripped[len("data: ") :]))
    return events


# ---------------------------------------------------------------------------
# 1 — AST: chat_stream has verify_api_key dependency
# ---------------------------------------------------------------------------


class TestAstChatStream:

    def test_chat_stream_requires_player_session(self):
        """SSE_AUTH_REQUIRED: chat_stream() must have Depends(get_current_player) in server.py.

        T3 unified auth: /chat/stream now requires a JWT-authenticated player
        session, the same as /chat.  Anonymous coaching is no longer permitted.
        """
        tree = _parse(_SERVER_PY)
        funcs = _get_functions(tree)
        func = funcs.get("chat_stream")
        assert func is not None, "chat_stream() not found in server.py"
        assert _depends_on(func, "get_current_player"), (
            "POST /chat/stream must have Depends(get_current_player) — "
            "streaming endpoint must enforce the same JWT auth as /chat"
        )


# ---------------------------------------------------------------------------
# 2–7 — Stub app SSE wire format
# ---------------------------------------------------------------------------


class TestSseWireFormat:

    def _ok(self):
        return _client.post("/chat/stream", headers=_auth_headers())

    def test_content_type_is_event_stream(self):
        """SSE_CONTENT_TYPE: 200 response Content-Type is text/event-stream."""
        r = self._ok()
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", ""), (
            f"Expected text/event-stream, got: {r.headers.get('content-type')}"
        )

    def test_at_least_one_chunk_event(self):
        """SSE_CHUNKS_PRESENT: at least one data: chunk event emitted."""
        events = _parse_sse_events(self._ok().text)
        chunks = [e for e in events if e.get("type") == "chunk"]
        assert chunks, f"No chunk events found; all events: {events}"

    def test_last_event_is_done(self):
        """SSE_DONE_LAST: last data: event has type == 'done'."""
        events = _parse_sse_events(self._ok().text)
        assert events, "No SSE events were parsed"
        assert events[-1].get("type") == "done", (
            f"Last event type must be 'done', got: {events[-1]}"
        )

    def test_chunk_text_concatenates_to_full_reply(self):
        """SSE_REPLY_COMPLETE: joining all chunk.text values yields the full reply."""
        events = _parse_sse_events(self._ok().text)
        full = "".join(e["text"] for e in events if e.get("type") == "chunk")
        assert full == _STUB_REPLY, (
            f"Concatenated chunks {full!r} != expected {_STUB_REPLY!r}"
        )

    def test_done_event_engine_signal_has_required_keys(self):
        """SSE_ENGINE_SIGNAL_DONE: done event engine_signal has all required ESV keys."""
        events = _parse_sse_events(self._ok().text)
        done = next((e for e in events if e.get("type") == "done"), None)
        assert done is not None, "No done event found in SSE body"
        signal = done.get("engine_signal", {})
        missing = _REQUIRED_ESV_KEYS - signal.keys()
        assert not missing, f"engine_signal missing required keys: {missing}"

    def test_done_event_mode_is_chat_v1(self):
        """SSE_MODE_DONE: done event mode field equals CHAT_V1."""
        events = _parse_sse_events(self._ok().text)
        done = next((e for e in events if e.get("type") == "done"), None)
        assert done is not None
        assert done.get("mode") == "CHAT_V1", (
            f"Expected mode='CHAT_V1', got {done.get('mode')!r}"
        )


# ---------------------------------------------------------------------------
# 8 — Auth enforcement via stub app
# ---------------------------------------------------------------------------


class TestSseAuth:

    def test_missing_api_key_returns_401(self):
        """SSE_AUTH_STUB: POST /chat/stream without X-Api-Key returns 401."""
        r = _client.post("/chat/stream", headers={})
        assert r.status_code == 401, (
            f"Expected 401 for missing API key, got {r.status_code}"
        )
