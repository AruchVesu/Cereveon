"""Contract tests for GET /game/history provenance filtering.

The Android history screen (2026-07-03) offers All / In-app / Lichess
tabs so imported games are visible, labelled, and reachable independently
of how many recent in-app games sit above them.  That rests on two
additive backend changes pinned here:

  * every row carries a ``source`` field (``"lichess"`` / ``"app"``),
    with legacy NULL-source rows normalised to ``"app"``;
  * an optional ``?source=`` filter (pattern-gated to ``app`` / ``lichess``)
    plus a bounded ``?limit=`` (default 20, 1..100).

Driven through a real FastAPI TestClient (auth + db overridden) so the
query-param validation — the 422s on a bad ``source`` / out-of-range
``limit`` — is exercised as the actual HTTP contract, not just the
handler body.

Pinned invariants
-----------------
GH_01  Unfiltered: all sources returned; NULL-source row normalises to "app".
GH_02  ?source=lichess returns only imported rows.
GH_03  ?source=app returns in-app rows INCLUDING legacy NULL-source, excludes lichess.
GH_04  ?limit caps the row count.
GH_05  Default limit is 20 (unchanged for older clients).
GH_06  ?source outside {app,lichess} → 422.
GH_07  ?limit out of [1,100] → 422.
GH_08  source filtering is player-scoped (never leaks another player's games).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm.seca.auth.models import Base
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401

from llm.seca.auth.router import get_current_player, get_db
from llm.seca.events.models import GameEvent
from llm.seca.events.router import router as game_router

_VALID_PGN = (
    '[Event "Test"]\n'
    '[Site "?"]\n'
    '[Date "2026.01.01"]\n'
    '[Round "1"]\n'
    '[White "Tester"]\n'
    '[Black "Bot"]\n'
    '[Result "1-0"]\n'
    "\n"
    "1. e4 e5 2. Nf3 Nc6 1-0"
)

_PLAYER_ID = "player-hist-1"
_OTHER_ID = "player-hist-2"


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _add_game(db, *, player_id=_PLAYER_ID, source, external_id=None):
    ev = GameEvent(
        player_id=player_id,
        pgn=_VALID_PGN,
        result="win",
        accuracy=0.5,
        weaknesses_json="{}",
        source=source,
        external_game_id=external_id,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def _client(db_session, player_id=_PLAYER_ID):
    app = FastAPI()
    app.include_router(game_router)
    # get_db is a generator dependency; the override yields our test
    # session and never closes it (the fixture owns teardown).
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_player] = lambda: SimpleNamespace(id=player_id)
    return TestClient(app)


def _sources(payload):
    return [g["source"] for g in payload["games"]]


# ---------------------------------------------------------------------------


def test_gh_01_unfiltered_returns_all_sources_and_normalises_null(db_session):
    _add_game(db_session, source=None)  # legacy in-app row
    _add_game(db_session, source="app")  # explicitly tagged in-app
    _add_game(db_session, source="lichess", external_id="lg1")

    resp = _client(db_session).get("/game/history")
    assert resp.status_code == 200
    games = resp.json()["games"]
    assert len(games) == 3
    # NULL normalises to "app"; every row has a concrete label.
    assert sorted(_sources(resp.json())) == ["app", "app", "lichess"]
    assert all(g["source"] in ("app", "lichess") for g in games)


def test_gh_02_filter_lichess_returns_only_imported(db_session):
    _add_game(db_session, source=None)
    _add_game(db_session, source="lichess", external_id="lg1")
    _add_game(db_session, source="lichess", external_id="lg2")

    payload = _client(db_session).get("/game/history", params={"source": "lichess"}).json()
    assert len(payload["games"]) == 2
    assert set(_sources(payload)) == {"lichess"}


def test_gh_03_filter_app_includes_legacy_null_excludes_lichess(db_session):
    _add_game(db_session, source=None)  # legacy in-app
    _add_game(db_session, source="app")  # explicit in-app
    _add_game(db_session, source="lichess", external_id="lg1")

    payload = _client(db_session).get("/game/history", params={"source": "app"}).json()
    assert len(payload["games"]) == 2
    assert set(_sources(payload)) == {"app"}  # both normalise to "app"


def test_gh_04_limit_caps_row_count(db_session):
    for i in range(5):
        _add_game(db_session, source="lichess", external_id=f"lg{i}")
    payload = _client(db_session).get("/game/history", params={"limit": 2}).json()
    assert len(payload["games"]) == 2


def test_gh_05_default_limit_is_20(db_session):
    for i in range(25):
        _add_game(db_session, source="lichess", external_id=f"lg{i}")
    payload = _client(db_session).get("/game/history").json()
    assert len(payload["games"]) == 20


def test_gh_06_bad_source_is_422(db_session):
    _add_game(db_session, source="lichess", external_id="lg1")
    resp = _client(db_session).get("/game/history", params={"source": "chesscom"})
    assert resp.status_code == 422


def test_gh_07_limit_out_of_range_is_422(db_session):
    resp_low = _client(db_session).get("/game/history", params={"limit": 0})
    resp_high = _client(db_session).get("/game/history", params={"limit": 101})
    assert resp_low.status_code == 422
    assert resp_high.status_code == 422


def test_gh_08_source_filter_is_player_scoped(db_session):
    # Another player's Lichess game must never surface for _PLAYER_ID.
    _add_game(db_session, player_id=_OTHER_ID, source="lichess", external_id="other-lg")
    _add_game(db_session, source="lichess", external_id="mine-lg")

    payload = _client(db_session).get("/game/history", params={"source": "lichess"}).json()
    assert len(payload["games"]) == 1
    assert payload["games"][0]["id"] != "other-lg"
