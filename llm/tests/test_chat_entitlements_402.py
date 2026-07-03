"""Endpoint-tier tests for the /chat + /chat/stream 402 quota gate (Subtask 4).

Both chat routes now pre-check the plan's daily chat quota via
``entitlements.check`` (over limit → the documented Shape B 402 body)
and consume a turn via ``entitlements.record`` only at the same
side-effect boundary as history persistence.  Pinned here:

1.  Flag OFF (shipping default): unlimited chat, no 402, zero usage
    rows — behaviour identical to before this subtask.
2.  Flag ON, free plan: turns 1–3 return the normal reply dict; turn 4
    is a ``JSONResponse`` with status 402 and EXACTLY the documented
    keys (``error`` / ``plan`` / ``limit`` / ``used`` / ``upgrade``);
    the 402 carries no ``X-Auth-Token`` header (route-level pin — the
    middleware's 2xx-only rotation contract has its own tests).
3.  A pipeline failure (5xx path) never consumes a turn: ``record``
    runs strictly after boundary validation.
4.  /chat/stream over limit returns a plain HTTP 402 JSON response
    BEFORE any stream is built — a ``JSONResponse``, never a
    ``StreamingResponse`` / SSE ``abort``.
5.  /chat/stream consumes the turn at the terminal event (``done`` and
    ``abort`` both — the user saw a reply either way), exactly where
    persistence happens, via a fresh session + detached-safe player
    snapshot.
6.  Pro plan: blocked at the 100/day soft cap, allowed at 99.

Direct endpoint-call style per test_game_checkpoint.py /
test_live_move_entitlements.py: shared limiter disabled, fake Starlette
request, pipeline fakes, ``engine_pool`` pinned to None.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.seca.auth.models import Base, Player
from llm.seca.chat.models import ChatTurn
from llm.seca.coach.chat_stream_pipeline import StreamAbort, StreamChunk, StreamDone
from llm.seca.entitlements.models import UsageCounter

_VALID_FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
_SIGNAL = extract_engine_signal({}, fen=_VALID_FEN)

# Canonical deterministic phrasing — passes every Mode-2 boundary gate.
_SAFE_REPLY = "The position is roughly equal."


def _today() -> str:
    """Mirror of the service's daily period_key format (documented on
    UsageCounter.period_key)."""
    return datetime.utcnow().strftime("%Y-%m-%d")


class _ChatPipelineSpy:
    """Stands in for generate_chat_reply on both routes."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.raise_exc: Exception | None = None

    def __call__(
        self,
        fen,
        turns,
        player_profile=None,
        past_mistakes=None,
        move_count=None,
        coach_voice=None,
        last_move=None,
        stockfish_json=None,
        force_deterministic=False,
    ):
        if self.raise_exc is not None:
            raise self.raise_exc
        self.calls.append({"force_deterministic": force_deterministic})
        return SimpleNamespace(reply=_SAFE_REPLY, engine_signal=_SIGNAL, mode="CHAT_V1")


def _fake_request() -> StarletteRequest:
    return StarletteRequest({
        "type": "http", "method": "POST", "path": "/chat",
        "headers": [], "client": ("127.0.0.1", 0),
    })


@pytest.fixture()
def db_env():
    """In-memory engine + two session handles (route + inspection)."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield SimpleNamespace(session=session, session_factory=session_factory)
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def db(db_env):
    return db_env.session


@pytest.fixture()
def player(db):
    p = Player(email="chat-402@test.com", password_hash="not-used-here")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def spy(monkeypatch):
    import llm.server as server
    from llm.seca.shared_limiter import limiter

    pipeline_spy = _ChatPipelineSpy()
    monkeypatch.setattr(server, "generate_chat_reply", pipeline_spy)
    monkeypatch.setattr(server, "engine_pool", None)
    monkeypatch.setattr(limiter, "enabled", False)
    return pipeline_spy


def _chat_request(content: str = "How is my position looking?"):
    import llm.server as server

    return server.ChatRequest(
        fen=_VALID_FEN, messages=[{"role": "user", "content": content}]
    )


def _call_chat(db, player):
    import llm.server as server

    return asyncio.run(
        server.chat(req=_chat_request(), request=_fake_request(), player=player, db=db)
    )


def _call_stream(db, player):
    import llm.server as server

    return asyncio.run(
        server.chat_stream(req=_chat_request(), request=_fake_request(), player=player, db=db)
    )


def _drain(streaming_response) -> str:
    async def _go():
        chunks = []
        async for chunk in streaming_response.body_iterator:
            chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
        return chunks

    return "".join(asyncio.run(_go()))


def _usage_rows(db) -> list[UsageCounter]:
    db.expire_all()  # see writes from the stream's fresh sessions
    return db.query(UsageCounter).all()


def _seed_chat_counter(db, player, count: int) -> None:
    db.add(
        UsageCounter(
            player_id=player.id, metric="chat_turn", period_key=_today(), count=count
        )
    )
    db.commit()


def _body_of(response: JSONResponse) -> dict:
    return json.loads(bytes(response.body))


# ---------------------------------------------------------------------------
# 1. Flag off — shipping default
# ---------------------------------------------------------------------------


class TestFlagOff:
    def test_unlimited_chat_and_no_rows(self, db, player, spy, monkeypatch):
        monkeypatch.delenv("SECA_ENTITLEMENTS_ENFORCED", raising=False)

        for _ in range(4):
            response = _call_chat(db, player)
            assert isinstance(response, dict), "flag off must never 402"
            assert response["reply"] == _SAFE_REPLY
            assert response["mode"] == "CHAT_V1"
        assert _usage_rows(db) == [], "dormant mode must not write usage rows"


# ---------------------------------------------------------------------------
# 2 + 3. Flag on — free plan /chat
# ---------------------------------------------------------------------------


class TestFlagOnFreeChat:
    def test_three_turns_ok_fourth_402_exact_body(self, db, player, spy, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")

        for turn in range(3):
            response = _call_chat(db, player)
            assert isinstance(response, dict), f"turn {turn + 1} should pass"

        blocked = _call_chat(db, player)
        assert isinstance(blocked, JSONResponse)
        assert blocked.status_code == 402
        body = _body_of(blocked)
        assert set(body.keys()) == {"error", "plan", "limit", "used", "upgrade"}, (
            "402 body must carry exactly the documented keys"
        )
        assert body["error"] == "chat_daily_limit"
        assert body["plan"] == "free"
        assert body["limit"] == 3
        assert body["used"] == 3
        assert body["upgrade"] == {"product": "pro_monthly"}
        assert "x-auth-token" not in blocked.headers, (
            "the 402 must never ship a rotation header"
        )
        # The gate runs before any pipeline work: 3 turns → 3 LLM calls.
        assert len(spy.calls) == 3

        rows = _usage_rows(db)
        assert len(rows) == 1 and rows[0].count == 3

    def test_pipeline_failure_consumes_no_turn(self, db, player, spy, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")

        assert isinstance(_call_chat(db, player), dict)  # turn 1 consumed
        assert _usage_rows(db)[0].count == 1

        spy.raise_exc = RuntimeError("forced pipeline failure")
        with pytest.raises(RuntimeError):
            _call_chat(db, player)
        assert _usage_rows(db)[0].count == 1, "a 5xx must not consume a turn"

        spy.raise_exc = None
        assert isinstance(_call_chat(db, player), dict), (
            "the failed request must not have used up quota"
        )
        assert _usage_rows(db)[0].count == 2


# ---------------------------------------------------------------------------
# 4 + 5. Flag on — /chat/stream
# ---------------------------------------------------------------------------


class TestFlagOnStream:
    def test_over_limit_returns_http_402_not_a_stream(self, db, player, spy, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")
        _seed_chat_counter(db, player, count=3)

        blocked = _call_stream(db, player)

        assert isinstance(blocked, JSONResponse), (
            "over-limit stream must be a plain HTTP error, not an SSE abort"
        )
        assert not isinstance(blocked, StreamingResponse)
        assert blocked.status_code == 402
        assert _body_of(blocked)["error"] == "chat_daily_limit"

    def _patch_stream(self, monkeypatch, db_env, events):
        import llm.seca.auth.router as auth_router
        import llm.server as server

        def _fake_stream(*args, **kwargs):
            yield from events

        monkeypatch.setattr(server, "stream_chat_reply", _fake_stream)
        # The stream's _persist/_record_turn closures open FRESH sessions
        # via auth_router.SessionLocal (the request session is closed by
        # generator-run time in production) — point them at this test's
        # in-memory engine.
        monkeypatch.setattr(auth_router, "SessionLocal", db_env.session_factory)

    def test_done_terminal_consumes_one_turn(self, db, db_env, player, spy, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")
        self._patch_stream(
            monkeypatch,
            db_env,
            [
                StreamChunk(text="The "),
                StreamDone(engine_signal=_SIGNAL, mode="CHAT_V1", reply=_SAFE_REPLY),
            ],
        )

        response = _call_stream(db, player)
        assert isinstance(response, StreamingResponse)
        wire = _drain(response)
        assert '"type": "done"' in wire

        rows = _usage_rows(db)
        assert len(rows) == 1 and rows[0].count == 1, (
            "the done terminal must consume exactly one turn"
        )
        db.expire_all()
        assert db.query(ChatTurn).count() == 2, (
            "counted and persisted must land together (user + assistant rows)"
        )

    def test_abort_terminal_still_consumes_turn(self, db, db_env, player, spy, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")
        self._patch_stream(monkeypatch, db_env, [StreamAbort(reason="forced by test")])

        response = _call_stream(db, player)
        wire = _drain(response)
        assert '"type": "abort"' in wire
        assert _SAFE_REPLY in wire, "abort serves the deterministic fallback reply"

        rows = _usage_rows(db)
        assert len(rows) == 1 and rows[0].count == 1, (
            "the user saw a (fallback) reply — the turn counts"
        )


# ---------------------------------------------------------------------------
# 6. Pro plan soft cap
# ---------------------------------------------------------------------------


class TestProPlan:
    def test_pro_allowed_at_99(self, db, player, spy, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")
        player.plan = "pro"
        db.commit()
        _seed_chat_counter(db, player, count=99)

        assert isinstance(_call_chat(db, player), dict)

    def test_pro_blocked_at_100(self, db, player, spy, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")
        player.plan = "pro"
        db.commit()
        _seed_chat_counter(db, player, count=100)

        blocked = _call_chat(db, player)
        assert isinstance(blocked, JSONResponse) and blocked.status_code == 402
        body = _body_of(blocked)
        assert (body["plan"], body["limit"], body["used"]) == ("pro", 100, 100)
