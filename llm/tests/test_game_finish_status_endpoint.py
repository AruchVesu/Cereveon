"""
Backend tests for ``GET /game/finish/{event_id}/status``.

Lays the polling contract that the future async-recompute work (the
"~2s Stockfish off the hot path" PR-2/3/4 series) will build on.
Today the endpoint is purely a retry-recovery surface: ``POST
/game/finish`` persists its response to ``game_finish_results``
synchronously, and this endpoint returns the same payload when the
client refetches by ``event_id``.

Why a separate test file
------------------------
``test_api_contract_validation.py`` covers the POST response shape
(now including ``event_id``); ``test_game_finish_db_integration.py``
covers ``EventStorage.store_game`` directly.  This file covers the
GET handler — auth, ownership, missing-row 404, oversized-id 400,
and the round-trip assertion that POST→GET returns the same payload.
Splitting keeps each file's concern narrow.

Pinned invariants
-----------------
 1. GFS_ROUND_TRIP_RETURNS_SAME_PAYLOAD
        After POST /game/finish persists, GET returns the same dict.
 2. GFS_GET_RESPONSE_CARRIES_EVENT_ID
        The GET payload also contains the ``event_id`` field that POST
        emits — sanity that the persisted JSON wasn't stripped.
 3. GFS_OWNERSHIP_403
        An authenticated caller whose ``player.id`` doesn't match the
        event's owner gets 403, not 404.
 4. GFS_UNKNOWN_EVENT_404
        A well-formed event_id that doesn't exist gets 404.
 5. GFS_MISSING_RESULT_ROW_202
        An event that exists but has no ``game_finish_results`` row
        (a pre-PR finish, a persistence failure, OR a future async-
        recompute path that hasn't completed yet) returns 202 +
        ``{status: "pending", event_id}`` so polling clients know
        to retry rather than give up.
 6. GFS_OVERSIZED_EVENT_ID_400
        An event_id longer than 64 chars rejects with 400 before any
        DB query, so a malicious probe can't waste a round-trip.
 7. GFS_CORRUPTED_JSON_500
        A row whose ``response_json`` doesn't parse surfaces as 500
        (with a logger.exception) — the contract is that 200s carry
        valid JSON, so a 200 with corrupted body would be worse.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import all SECA model modules so Base.metadata is complete before
# create_all picks up the new ``game_finish_results`` table.
from llm.seca.auth.models import Base  # noqa: E402
import llm.seca.auth.models  # noqa: F401,E402
import llm.seca.events.models  # noqa: F401,E402
import llm.seca.brain.models  # noqa: F401,E402
import llm.seca.analytics.models  # noqa: F401,E402

from llm.seca.events.models import GameEvent, GameFinishResult  # noqa: E402
from llm.seca.events.router import game_finish_status  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures — real SQLite session, real ORM round-trip.
#
# The endpoint reads from two tables (game_events + game_finish_results)
# and applies an ownership filter.  Faking these via MagicMock would
# require so much chain-of-mocks plumbing that the test wouldn't
# actually exercise the SQL.  In-memory SQLite is cheap and the
# integration test is what the production failure mode actually
# resembles.
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_event_and_result(
    session,
    *,
    player_id: str = "player-A",
    event_id: str = "event-1",
    response_payload: dict | None = None,
) -> tuple[GameEvent, GameFinishResult | None]:
    """Insert a GameEvent + optionally a GameFinishResult row.

    Returns (event, result) where ``result`` is None when
    ``response_payload`` is None — that simulates the pre-PR / failed-
    persistence path the GFS_MISSING_RESULT_ROW_404 test exercises.
    """
    event = GameEvent(
        id=event_id,
        player_id=player_id,
        pgn="dummy-pgn",
        result="win",
        accuracy=0.5,
        weaknesses_json="{}",
    )
    session.add(event)

    result = None
    if response_payload is not None:
        result = GameFinishResult(
            event_id=event_id,
            response_json=json.dumps(response_payload),
        )
        session.add(result)

    session.commit()
    return event, result


def _player(player_id: str):
    """Minimal player namespace — the handler only reads ``.id``."""
    return SimpleNamespace(id=player_id)


# ---------------------------------------------------------------------------
# GFS_ROUND_TRIP — POST→GET returns the same payload.
# ---------------------------------------------------------------------------


def test_GFS_ROUND_TRIP_RETURNS_SAME_PAYLOAD(db_session):
    """The handler must decode and return exactly the dict that was
    persisted by ``finish_game``.  Catches a future "let me wrap the
    response in another envelope" change that would silently break
    every Android client built against the synchronous-POST shape."""
    payload = {
        "status": "stored",
        "event_id": "event-1",
        "new_rating": 1512.0,
        "confidence": 0.72,
        "coach_action": {"type": "REFLECT", "weakness": None, "reason": "stable"},
        "biggest_mistake": None,
    }
    _make_event_and_result(
        db_session,
        player_id="player-A",
        event_id="event-1",
        response_payload=payload,
    )

    out = game_finish_status(
        event_id="event-1",
        player=_player("player-A"),
        db=db_session,
    )
    assert out == payload, (
        "GET payload diverged from persisted POST response.  If JSON "
        "round-trip is now lossy, fix ``response_json`` encoding in "
        "finish_game; do NOT widen this assertion to ignore the delta."
    )


def test_GFS_GET_RESPONSE_CARRIES_EVENT_ID(db_session):
    """The event_id field round-trips through the persistence layer.
    Pinned separately so a future refactor that drops event_id from
    the POST persistence step fails this test loudly (the round-trip
    test above would still pass because both sides would be missing
    the field)."""
    payload = {"status": "stored", "event_id": "event-with-id", "new_rating": 1500.0}
    _make_event_and_result(
        db_session,
        player_id="player-A",
        event_id="event-with-id",
        response_payload=payload,
    )

    out = game_finish_status(
        event_id="event-with-id",
        player=_player("player-A"),
        db=db_session,
    )
    assert out["event_id"] == "event-with-id"


# ---------------------------------------------------------------------------
# GFS_OWNERSHIP — 403, not 404, when a different player asks.
# ---------------------------------------------------------------------------


def test_GFS_OWNERSHIP_403(db_session):
    """403 (not 404) when the event exists but belongs to a different
    player.  Distinct from 404 so operators investigating an access-
    log probe can tell "user is looking up something they don't own"
    apart from "id space scanner".  Same convention as the POST
    handler's ``Cannot submit game for another player`` 403."""
    _make_event_and_result(
        db_session,
        player_id="player-A",
        event_id="event-A1",
        response_payload={"status": "stored"},
    )

    with pytest.raises(HTTPException) as exc_info:
        game_finish_status(
            event_id="event-A1",
            player=_player("player-B"),
            db=db_session,
        )
    assert exc_info.value.status_code == 403
    assert "another player" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# GFS_UNKNOWN_EVENT — well-formed id, no such event.
# ---------------------------------------------------------------------------


def test_GFS_UNKNOWN_EVENT_404(db_session):
    """404 when the event_id doesn't match any GameEvent row.  Note
    this fires BEFORE the result-row lookup so an attacker can't
    enumerate event ids by timing the response."""
    # Nothing inserted — the event_id is well-formed but has no row.
    with pytest.raises(HTTPException) as exc_info:
        game_finish_status(
            event_id="event-does-not-exist",
            player=_player("player-A"),
            db=db_session,
        )
    assert exc_info.value.status_code == 404
    assert "event not found" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# GFS_MISSING_RESULT_ROW — event exists, result row doesn't.
# ---------------------------------------------------------------------------


def test_GFS_MISSING_RESULT_ROW_202(db_session):
    """202 + {status: "pending", event_id} when the GameEvent exists
    but no GameFinishResult was persisted.

    Three causes all reach this branch and the server cannot
    distinguish them:
      * legacy events that completed before PR #195 shipped
      * finishes whose best-effort GameFinishResult persistence
        inside ``finish_game`` raised and rolled back
      * future async-recompute finishes whose background task
        hasn't written the result row yet

    All three share the polling-client contract: the result is not
    available right now; retry.  The 202 (not 404) status signals
    polling clients to keep trying rather than give up.

    Pre-PR-2 (today's code, before this commit) returned 404 here;
    the test was updated together with the route change."""
    _make_event_and_result(
        db_session,
        player_id="player-A",
        event_id="event-no-result",
        response_payload=None,  # no GameFinishResult written
    )

    # The handler now returns a JSONResponse (instead of raising
    # HTTPException) when the result row is missing — pollers need
    # the body shape, not just the status code.
    from fastapi.responses import JSONResponse

    out = game_finish_status(
        event_id="event-no-result",
        player=_player("player-A"),
        db=db_session,
    )
    assert isinstance(out, JSONResponse), (
        "Expected JSONResponse so the pending shape can carry a body; "
        f"got {type(out).__name__}.  If you intentionally changed the "
        "shape, update this test in the same commit."
    )
    assert out.status_code == 202
    body = json.loads(out.body)
    assert body == {"status": "pending", "event_id": "event-no-result"}, (
        f"Pending body shape must be {{status, event_id}}; got {body!r}.  "
        "Polling clients depend on this exact shape."
    )


# ---------------------------------------------------------------------------
# GFS_OVERSIZED_EVENT_ID — defensive cap.
# ---------------------------------------------------------------------------


def test_GFS_OVERSIZED_EVENT_ID_400(db_session):
    """An event_id longer than the documented 64-char cap rejects
    with 400 before any DB query.  Catches a missing length check
    that would otherwise let a 10 MB probe waste a query plan."""
    huge_id = "x" * 65

    with pytest.raises(HTTPException) as exc_info:
        game_finish_status(
            event_id=huge_id,
            player=_player("player-A"),
            db=db_session,
        )
    assert exc_info.value.status_code == 400
    assert "too long" in exc_info.value.detail.lower()


def test_GFS_OVERSIZED_EVENT_ID_400_no_db_query(db_session):
    """The oversized-id 400 fires BEFORE any DB read — pin by
    asserting the GameEvents table is untouched after the call.
    If a future refactor moves the cap check below the query, this
    test catches it (and the "wastes a query plan" rationale in the
    handler comment becomes visible to the next reviewer).
    """
    huge_id = "y" * 200
    before_count = db_session.query(GameEvent).count()
    with pytest.raises(HTTPException):
        game_finish_status(
            event_id=huge_id,
            player=_player("player-A"),
            db=db_session,
        )
    after_count = db_session.query(GameEvent).count()
    assert before_count == after_count, (
        "Oversized event_id should reject before any DB write/read.  "
        "The handler's defensive cap check must precede the GameEvent "
        "query."
    )


# ---------------------------------------------------------------------------
# GFS_CORRUPTED_JSON — defensive 500 on unreadable persisted row.
# ---------------------------------------------------------------------------


def test_GFS_CORRUPTED_JSON_500(db_session):
    """A persisted row whose ``response_json`` is corrupted (not
    valid JSON) surfaces as 500.  We could fall back to returning
    {} or re-deriving the payload, but both options hide a contract
    violation — the rest of this PR guarantees ``response_json`` is
    always ``json.dumps`` output, so a corrupted row indicates either
    DB corruption or a future contributor bypassing the
    ``json.dumps`` step.  500 + ``logger.exception`` is the alert
    path; the GET endpoint must not paper over the inconsistency."""
    event = GameEvent(
        id="event-corrupt",
        player_id="player-A",
        pgn="pgn",
        result="win",
        accuracy=0.5,
        weaknesses_json="{}",
    )
    db_session.add(event)
    db_session.add(
        GameFinishResult(
            event_id="event-corrupt",
            response_json="{not-valid-json",
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        game_finish_status(
            event_id="event-corrupt",
            player=_player("player-A"),
            db=db_session,
        )
    assert exc_info.value.status_code == 500
    assert "unreadable" in exc_info.value.detail.lower()
