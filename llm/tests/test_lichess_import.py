"""Tests for the Lichess ingestion adapter.

Covers three layers:

* ``llm.seca.lichess.client``         — HTTP-error translation, NDJSON streaming,
                                        defensive byte caps, evals=false pin.
* ``llm.seca.lichess.import_service`` — link / unlink / status, dedup,
                                        result derivation, first-link
                                        calibration semantics, PGN sanity.
* ``llm.seca.lichess.router``         — schema validation, exception → HTTP
                                        translation, auth integration.

httpx is mocked at the function level (``httpx.Client``, ``httpx.stream``)
rather than via the network so tests are deterministic and need no live
Lichess credentials.  The router-layer tests call handlers directly
(matching the pattern in ``test_auth_update_me.py``) to bypass FastAPI
dependency injection and slowapi rate limits.

Pinned invariants
-----------------
CL_01  fetch_user_profile happy path returns parsed JSON.
CL_02  404 from Lichess → LichessUserNotFound.
CL_03  429 with Retry-After → LichessRateLimited carrying retry_after.
CL_04  5xx → LichessUpstreamError.
CL_05  Malformed JSON body → LichessParseError.
CL_06  fetch_user_games yields one dict per NDJSON line, skipping blanks.
CL_07  fetch_user_games pins ``evals=false`` (architecture invariant).
CL_08  fetch_user_games rejects max_games > MAX_GAMES_PER_REQUEST.
CL_09  Per-line byte cap raises LichessParseError.
CL_10  Empty username rejected pre-flight.

IS_01  link_account inserts a LinkedAccount row.
IS_02  link_account stores Lichess canonical lowercase id, not the raw input.
IS_03  link_account second call with same handle replaces (no duplicate).
IS_04  link_account rejects when another player already owns the handle.
IS_04b link_account with claim_from_other_player=True takes the link over
       (OAuth-verified owner) and cancels the losing account's jobs.
IS_05  First-link calibration fires on default-rated player.
IS_06  First-link calibration is skipped for a non-default player.
IS_07  unlink_account removes the row; imported GameEvents are retained.
IS_08  get_status returns linked=False when no link.
IS_09  get_status returns counts and watermark when linked.
IS_10  import_user_games inserts GameEvent rows with source='lichess'.
IS_11  import_user_games dedups by (source, external_game_id).
IS_12  import_user_games derives win/loss/draw from winner + player color.
IS_13  import_user_games skips games with invalid PGN, oversize, missing id.
IS_14  import_user_games advances last_imported_at watermark.
IS_15  import_user_games on an unlinked player raises LichessNotLinkedError.
IS_16  _derive_result handles white/black/draw plus missing-user case.
IS_17  _derive_player_color returns the user's side (white/black/none),
       for replay board orientation.

RT_01  POST /lichess/link rejects malformed usernames at the schema layer.
RT_02  POST /lichess/link 404 propagates LichessUserNotFound.
RT_03  POST /lichess/link 409 on cross-player conflict.
RT_04  GET /lichess/status mirrors service output.
RT_05  POST /lichess/import returns counts.
RT_06  Service-layer errors map to documented HTTP codes.

DC_01..DC_08  Disconnect detection + reconnect resolution — see the
       section comment above ``TestDisconnectReconnect``.
"""

from __future__ import annotations

import io
import json
import os
from contextlib import contextmanager
from typing import Iterable

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request as StarletteRequest

# Import all model modules so Base.metadata sees every table.
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401
import llm.seca.lichess.models  # noqa: F401
import llm.seca.notifications.models  # noqa: F401

from llm.seca.auth.models import Base, Player
from llm.seca.events.models import GameEvent
from llm.seca.lichess import client as lichess_client
from llm.seca.lichess import import_service
from llm.seca.lichess.import_service import (
    LichessAlreadyLinkedError,
    LichessImportError,
    LichessNotLinkedError,
)
from llm.seca.lichess.models import LinkedAccount
from llm.seca.lichess.router import LinkRequest, link, status, trigger_import, unlink
from llm.seca.shared_limiter import limiter

# ---------------------------------------------------------------------------
# Shared fixtures + mock infrastructure
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def player(db_session):
    p = Player(
        email="liu@test.com",
        password_hash="dummy",
        rating=1200.0,
        confidence=0.5,
        skill_vector_json="{}",
        player_embedding="[]",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture()
def other_player(db_session):
    p = Player(
        email="other@test.com",
        password_hash="dummy",
        rating=1200.0,
        confidence=0.5,
        skill_vector_json="{}",
        player_embedding="[]",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _fake_request(headers: list[tuple[bytes, bytes]] | None = None) -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/lichess/link",
            "headers": headers or [],
            "client": ("127.0.0.1", 0),
        }
    )


def _fake_response():
    """Stub ``fastapi.Response`` for handlers that set status_code via DI.

    FastAPI normally injects a fresh ``Response`` per request; tests
    that call the handler directly (bypassing the router) need to
    construct one manually.
    """
    from fastapi import Response  # local import to keep top-of-file lean

    return Response()


@contextmanager
def _limiter_disabled():
    prev = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = prev


# ---- httpx mock helpers ---------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, body: object = None, headers: dict | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, (dict, list)):
            return self._body
        if isinstance(self._body, str):
            return json.loads(self._body)
        raise ValueError("no JSON body")


class _FakeClientCM:
    """Stand-in for ``httpx.Client(...)`` context manager."""

    def __init__(self, response: _FakeResponse):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None):
        return self._response


class _FakeStreamCM:
    """Stand-in for the context manager returned by ``httpx.stream(...)``."""

    def __init__(self, response: _FakeResponse, lines: Iterable[str]):
        self._response = response
        self._lines = list(lines)

    def __enter__(self):
        # The real response object also iter_lines from inside __enter__.
        self._response.iter_lines = lambda: iter(self._lines)
        return self._response

    def __exit__(self, *a):
        return False


@pytest.fixture()
def patch_profile(monkeypatch):
    """Helper to monkey-patch ``httpx.Client`` returning a canned profile."""

    def _apply(*, status_code: int, body: object = None, headers: dict | None = None):
        response = _FakeResponse(status_code=status_code, body=body, headers=headers or {})
        monkeypatch.setattr(
            lichess_client.httpx,
            "Client",
            lambda **kw: _FakeClientCM(response),
        )

    return _apply


@pytest.fixture()
def patch_games(monkeypatch):
    """Helper to monkey-patch ``httpx.stream`` returning canned NDJSON lines."""

    captured: dict = {}

    def _apply(*, status_code: int = 200, lines: Iterable[str] = (), headers: dict | None = None):
        response = _FakeResponse(status_code=status_code, headers=headers or {})

        def fake_stream(method, url, **kw):
            captured["method"] = method
            captured["url"] = url
            captured["params"] = kw.get("params", {})
            captured["headers"] = kw.get("headers", {})
            return _FakeStreamCM(response, lines)

        monkeypatch.setattr(lichess_client.httpx, "stream", fake_stream)
        return captured

    return _apply


# Representative valid PGN — copied from the existing /game/finish test
# fixtures so the python-chess parser accepts it.
_VALID_PGN = (
    '[Event "Casual"]\n'
    '[Site "https://lichess.org/abc12345"]\n'
    '[Date "2026.01.01"]\n'
    '[White "alice"]\n'
    '[Black "bob"]\n'
    '[Result "1-0"]\n'
    "\n"
    "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0\n"
)


def _game_dict(
    *,
    external_id: str,
    white: str,
    black: str,
    winner: str | None,
    created_at_ms: int = 1_700_000_000_000,
    pgn: str = _VALID_PGN,
) -> dict:
    payload = {
        "id": external_id,
        "rated": True,
        "speed": "blitz",
        "perf": "blitz",
        "createdAt": created_at_ms,
        "players": {
            "white": {"user": {"id": white, "name": white}, "rating": 1500},
            "black": {"user": {"id": black, "name": black}, "rating": 1500},
        },
        "pgn": pgn,
    }
    if winner is not None:
        payload["winner"] = winner
    return payload


# ===========================================================================
# Client layer
# ===========================================================================


class TestClient:
    # CL_01
    def test_profile_happy_path(self, patch_profile):
        patch_profile(
            status_code=200,
            body={"id": "alice", "username": "Alice", "perfs": {}},
        )
        result = lichess_client.fetch_user_profile("Alice")
        assert result["id"] == "alice"

    # CL_02
    def test_profile_404_raises_user_not_found(self, patch_profile):
        patch_profile(status_code=404)
        with pytest.raises(lichess_client.LichessUserNotFound):
            lichess_client.fetch_user_profile("ghost")

    # CL_03
    def test_profile_429_raises_rate_limited_with_retry_after(self, patch_profile):
        patch_profile(status_code=429, headers={"Retry-After": "42"})
        with pytest.raises(lichess_client.LichessRateLimited) as excinfo:
            lichess_client.fetch_user_profile("alice")
        assert excinfo.value.retry_after == 42

    # CL_03 — non-integer Retry-After is tolerated.
    def test_profile_429_non_integer_retry_after_is_none(self, patch_profile):
        patch_profile(status_code=429, headers={"Retry-After": "soon"})
        with pytest.raises(lichess_client.LichessRateLimited) as excinfo:
            lichess_client.fetch_user_profile("alice")
        assert excinfo.value.retry_after is None

    # CL_04
    def test_profile_5xx_raises_upstream(self, patch_profile):
        patch_profile(status_code=503)
        with pytest.raises(lichess_client.LichessUpstreamError):
            lichess_client.fetch_user_profile("alice")

    # CL_05
    def test_profile_malformed_body_raises_parse(self, patch_profile, monkeypatch):
        class _BadResp(_FakeResponse):
            def json(self):
                raise json.JSONDecodeError("bad", "", 0)

        bad = _BadResp(status_code=200, body=None)
        monkeypatch.setattr(lichess_client.httpx, "Client", lambda **kw: _FakeClientCM(bad))
        with pytest.raises(lichess_client.LichessParseError):
            lichess_client.fetch_user_profile("alice")

    # CL_06
    def test_games_stream_yields_one_dict_per_line(self, patch_games):
        patch_games(
            lines=[
                json.dumps({"id": "abc", "pgn": "..."}),
                "",  # blank — must be skipped
                "   ",  # whitespace — must be skipped
                json.dumps({"id": "def", "pgn": "..."}),
            ]
        )
        games = list(lichess_client.fetch_user_games("alice", max_games=10))
        assert [g["id"] for g in games] == ["abc", "def"]

    # CL_07 — evals=false is the architecture pin.
    def test_games_pins_evals_false(self, patch_games):
        captured = patch_games(lines=[])
        list(lichess_client.fetch_user_games("alice", max_games=5))
        assert captured["params"]["evals"] == "false"

    # CL_07 — pgnInJson + clocks + opening are sent so the import service
    # can read structured fields without re-parsing PGN.
    def test_games_pins_pgnInJson_and_friends(self, patch_games):
        captured = patch_games(lines=[])
        list(lichess_client.fetch_user_games("alice", max_games=5))
        assert captured["params"]["pgnInJson"] == "true"
        assert captured["params"]["clocks"] == "true"
        assert captured["params"]["opening"] == "true"

    # CL_07 — since_ms forwarded as 'since' query param.
    def test_games_forwards_since_ms(self, patch_games):
        captured = patch_games(lines=[])
        list(lichess_client.fetch_user_games("alice", max_games=5, since_ms=1_700_000_000_000))
        assert captured["params"]["since"] == "1700000000000"

    # CL_08
    def test_games_rejects_max_games_above_cap(self):
        with pytest.raises(ValueError):
            list(
                lichess_client.fetch_user_games(
                    "alice", max_games=lichess_client.MAX_GAMES_PER_REQUEST + 1
                )
            )

    # CL_08 — non-positive max rejected
    def test_games_rejects_non_positive_max(self):
        with pytest.raises(ValueError):
            list(lichess_client.fetch_user_games("alice", max_games=0))

    # CL_09
    def test_games_oversize_line_raises_parse(self, patch_games):
        huge = "x" * (lichess_client._MAX_NDJSON_LINE_BYTES + 1)
        patch_games(lines=[huge])
        with pytest.raises(lichess_client.LichessParseError):
            list(lichess_client.fetch_user_games("alice", max_games=5))

    # CL_10
    def test_empty_username_rejected_profile(self):
        with pytest.raises(ValueError):
            lichess_client.fetch_user_profile("   ")

    # CL_10
    def test_empty_username_rejected_games(self):
        with pytest.raises(ValueError):
            list(lichess_client.fetch_user_games("   ", max_games=5))

    # CL_11 — SSRF defense in depth.  The router layer pre-validates
    # via _LICHESS_USERNAME_RE, but the client must independently reject
    # any input that could escape the URL path or inject query state,
    # so a future internal caller cannot smuggle a path-traversal /
    # open-redirect payload through.  CodeQL flagged the missing check
    # on the first cut as "partial server-side request forgery".
    @pytest.mark.parametrize(
        "ssrf_shaped",
        [
            "alice/../admin",  # path traversal
            "alice?evil=1",  # query injection
            "alice#frag",  # fragment injection
            "alice%2F..",  # encoded slash
            "alice evil",  # whitespace
            "ümlaut",  # non-ASCII
            "alice@evil.com",  # @-hijack
            ".",  # bare dot
            "..",  # double dot
            "a",  # too short
            "x" * 31,  # too long
        ],
    )
    def test_client_rejects_ssrf_shaped_username_profile(self, ssrf_shaped):
        with pytest.raises(ValueError):
            lichess_client.fetch_user_profile(ssrf_shaped)

    @pytest.mark.parametrize(
        "ssrf_shaped",
        [
            "alice/../admin",
            "alice?evil=1",
            "alice#frag",
            "ümlaut",
        ],
    )
    def test_client_rejects_ssrf_shaped_username_games(self, ssrf_shaped):
        with pytest.raises(ValueError):
            list(lichess_client.fetch_user_games(ssrf_shaped, max_games=5))

    # CL_06 — malformed NDJSON line surfaces as parse error.
    def test_games_malformed_json_line_raises_parse(self, patch_games):
        patch_games(lines=["{not json"])
        with pytest.raises(lichess_client.LichessParseError):
            list(lichess_client.fetch_user_games("alice", max_games=5))


# ===========================================================================
# Import service — link / unlink / status / calibration
# ===========================================================================


def _stub_profile(monkeypatch, *, profile: dict):
    """Replace fetch_user_profile with a fixed return value."""
    monkeypatch.setattr(import_service.lichess_client, "fetch_user_profile", lambda u: profile)


def _stub_games(monkeypatch, *, games: list[dict]):
    """Replace fetch_user_games with a generator over the given dicts."""

    def _gen(username, **kwargs):
        for g in games:
            yield g

    monkeypatch.setattr(import_service.lichess_client, "fetch_user_games", _gen)


class TestLinkAccount:
    # IS_01
    def test_link_inserts_row(self, db_session, player, monkeypatch):
        _stub_profile(
            monkeypatch,
            profile={
                "id": "alice",
                "perfs": {"rapid": {"games": 100, "rating": 1700, "prov": False}},
            },
        )
        result = import_service.link_account(db_session, player, "Alice")
        assert result["platform"] == "lichess"
        rows = db_session.query(LinkedAccount).all()
        assert len(rows) == 1
        assert rows[0].external_username == "alice"

    # IS_02
    def test_link_stores_canonical_lowercase_id(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, player, "ALICE")
        row = db_session.query(LinkedAccount).one()
        assert row.external_username == "alice"

    # IS_03 — re-linking same handle replaces, no duplicates.
    def test_relink_replaces_existing(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, player, "alice")
        _stub_profile(monkeypatch, profile={"id": "alicebob", "perfs": {}})
        import_service.link_account(db_session, player, "alicebob")
        rows = db_session.query(LinkedAccount).filter_by(player_id=player.id).all()
        assert len(rows) == 1
        assert rows[0].external_username == "alicebob"

    # IS_04
    def test_link_rejects_when_another_player_owns_handle(
        self, db_session, player, other_player, monkeypatch
    ):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, other_player, "alice")
        with pytest.raises(LichessAlreadyLinkedError):
            import_service.link_account(db_session, player, "alice")

    # IS_04b — verified-owner (OAuth) claim takes the link over instead of
    # rejecting, and cancels the losing account's active import jobs.
    def test_link_claim_takes_over_from_another_player(
        self, db_session, player, other_player, monkeypatch
    ):
        from llm.seca.lichess.models import (
            JOB_STATUS_FAILED,
            JOB_STATUS_RUNNING,
            LichessImportJob,
        )

        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, other_player, "alice")
        db_session.add(
            LichessImportJob(
                player_id=other_player.id,
                status=JOB_STATUS_RUNNING,
                target_max_games=50,
            )
        )
        db_session.commit()

        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, player, "alice", claim_from_other_player=True)

        rows = db_session.query(LinkedAccount).filter_by(external_username="alice").all()
        assert len(rows) == 1
        assert rows[0].player_id == player.id  # link moved to the OAuth owner
        # The losing account's in-flight import job was cancelled.
        job = db_session.query(LichessImportJob).filter_by(player_id=other_player.id).one()
        assert job.status == JOB_STATUS_FAILED

    # IS_05 — default-rated player gets calibrated.
    def test_first_link_calibrates_default_player(self, db_session, player, monkeypatch):
        _stub_profile(
            monkeypatch,
            profile={
                "id": "alice",
                "perfs": {
                    "rapid": {"games": 250, "rating": 1900, "prov": False},
                    "blitz": {"games": 1000, "rating": 1700, "prov": False},
                },
            },
        )
        result = import_service.link_account(db_session, player, "alice")
        db_session.refresh(player)
        assert result["calibration"]["applied"] is True
        # Rapid is preferred over blitz.
        assert result["calibration"]["perf"] == "rapid"
        assert player.rating == 1900.0
        # 250 games non-provisional → 0.85.
        assert player.confidence == 0.85

    # IS_05 — provisional perf clamps confidence low.
    def test_first_link_provisional_lowers_confidence(self, db_session, player, monkeypatch):
        _stub_profile(
            monkeypatch,
            profile={
                "id": "alice",
                "perfs": {"rapid": {"games": 5, "rating": 1500, "prov": True}},
            },
        )
        import_service.link_account(db_session, player, "alice")
        db_session.refresh(player)
        assert player.confidence == 0.4

    # IS_05 — rating clamped to chess-realistic window.
    def test_first_link_clamps_rating(self, db_session, player, monkeypatch):
        _stub_profile(
            monkeypatch,
            profile={
                "id": "alice",
                "perfs": {"rapid": {"games": 100, "rating": 5000, "prov": False}},
            },
        )
        import_service.link_account(db_session, player, "alice")
        db_session.refresh(player)
        assert player.rating == import_service._CALIBRATION_RATING_MAX

    # IS_06 — non-default player is NOT recalibrated.
    def test_link_skips_calibration_on_non_default_player(self, db_session, player, monkeypatch):
        player.rating = 1600.0
        player.confidence = 0.7
        db_session.commit()
        _stub_profile(
            monkeypatch,
            profile={
                "id": "alice",
                "perfs": {"rapid": {"games": 200, "rating": 1900, "prov": False}},
            },
        )
        result = import_service.link_account(db_session, player, "alice")
        db_session.refresh(player)
        assert result["calibration"]["applied"] is False
        assert player.rating == 1600.0
        assert player.confidence == 0.7

    # IS_05 — perf with 0 games is skipped, falls through to next.
    def test_first_link_skips_empty_perf(self, db_session, player, monkeypatch):
        _stub_profile(
            monkeypatch,
            profile={
                "id": "alice",
                "perfs": {
                    "rapid": {"games": 0, "rating": 1500},  # empty
                    "blitz": {"games": 100, "rating": 1750, "prov": False},
                },
            },
        )
        result = import_service.link_account(db_session, player, "alice")
        assert result["calibration"]["perf"] == "blitz"

    # IS_05 — no eligible perf → no calibration, default values preserved.
    def test_first_link_no_eligible_perf(self, db_session, player, monkeypatch):
        _stub_profile(
            monkeypatch,
            profile={"id": "alice", "perfs": {"bullet": {"games": 100, "rating": 1500}}},
        )
        result = import_service.link_account(db_session, player, "alice")
        db_session.refresh(player)
        assert result["calibration"]["applied"] is False
        assert result["calibration"]["reason"] == "no_eligible_perf"
        assert player.rating == 1200.0

    def test_link_missing_id_field_raises(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"perfs": {}})  # no 'id'
        with pytest.raises(LichessImportError):
            import_service.link_account(db_session, player, "alice")


class TestUnlinkAndStatus:
    # IS_07
    def test_unlink_removes_row(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, player, "alice")
        assert import_service.unlink_account(db_session, player) is True
        assert db_session.query(LinkedAccount).count() == 0

    # IS_07 — imported game_events are NOT cascade-deleted.
    def test_unlink_retains_imported_games(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, player, "alice")
        db_session.add(
            GameEvent(
                player_id=player.id,
                pgn=_VALID_PGN,
                result="win",
                source="lichess",
                external_game_id="abc12345",
            )
        )
        db_session.commit()
        import_service.unlink_account(db_session, player)
        assert db_session.query(GameEvent).count() == 1

    def test_unlink_returns_false_when_no_link(self, db_session, player):
        assert import_service.unlink_account(db_session, player) is False

    # IS_08
    def test_status_unlinked(self, db_session, player):
        result = import_service.get_status(db_session, player)
        assert result == {"linked": False}

    # IS_09
    def test_status_linked_includes_counts(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, player, "alice")
        db_session.add(
            GameEvent(
                player_id=player.id,
                pgn=_VALID_PGN,
                result="win",
                source="lichess",
                external_game_id="g1",
            )
        )
        db_session.add(
            GameEvent(
                player_id=player.id,
                pgn=_VALID_PGN,
                result="loss",
                source="lichess",
                external_game_id="g2",
            )
        )
        # A non-lichess row must NOT inflate the imported_game_count.
        db_session.add(GameEvent(player_id=player.id, pgn=_VALID_PGN, result="win", source="app"))
        db_session.commit()
        result = import_service.get_status(db_session, player)
        assert result["linked"] is True
        assert result["external_username"] == "alice"
        assert result["imported_game_count"] == 2


# ===========================================================================
# Import service — import flow + dedup + result derivation
# ===========================================================================


class TestImportUserGames:
    @pytest.fixture()
    def linked_player(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, player, "alice")
        return player

    # IS_10
    def test_import_inserts_game_event_rows(self, db_session, linked_player, monkeypatch):
        _stub_games(
            monkeypatch,
            games=[
                _game_dict(external_id="g1", white="alice", black="bob", winner="white"),
                _game_dict(external_id="g2", white="bob", black="alice", winner="white"),
            ],
        )
        result = import_service.import_user_games(db_session, linked_player, max_games=10)
        assert result["inserted"] == 2
        rows = (
            db_session.query(GameEvent)
            .filter(GameEvent.source == "lichess")
            .order_by(GameEvent.external_game_id)
            .all()
        )
        assert [r.external_game_id for r in rows] == ["g1", "g2"]
        assert all(r.source == "lichess" for r in rows)
        g1 = next(r for r in rows if r.external_game_id == "g1")
        g2 = next(r for r in rows if r.external_game_id == "g2")
        # g1: alice was white, white won → win.
        assert g1.result == "win"
        # g2: alice was black, white won → loss.
        assert g2.result == "loss"
        # player_color is stored for replay orientation (IS_17 companion):
        # alice played white in g1, black in g2.
        assert g1.player_color == "white"
        assert g2.player_color == "black"

    # IS_11
    def test_import_dedups_on_external_game_id(self, db_session, linked_player, monkeypatch):
        _stub_games(
            monkeypatch,
            games=[
                _game_dict(external_id="g1", white="alice", black="bob", winner="white"),
            ],
        )
        import_service.import_user_games(db_session, linked_player, max_games=10)
        # Second call returns the same ID — must dedup.
        result = import_service.import_user_games(db_session, linked_player, max_games=10)
        assert result["inserted"] == 0
        assert result["skipped_duplicate"] == 1
        assert db_session.query(GameEvent).filter(GameEvent.source == "lichess").count() == 1

    # IS_12 — draw.
    def test_import_draw_derivation(self, db_session, linked_player, monkeypatch):
        _stub_games(
            monkeypatch,
            games=[
                _game_dict(external_id="g3", white="alice", black="bob", winner=None),
            ],
        )
        import_service.import_user_games(db_session, linked_player, max_games=10)
        row = db_session.query(GameEvent).filter(GameEvent.external_game_id == "g3").one()
        assert row.result == "draw"

    # IS_13 — invalid PGN is skipped, not fatal.
    def test_import_skips_invalid_pgn(self, db_session, linked_player, monkeypatch):
        bad = _game_dict(
            external_id="bad1", white="alice", black="bob", winner="white", pgn="not a pgn"
        )
        good = _game_dict(external_id="ok1", white="alice", black="bob", winner="white")
        _stub_games(monkeypatch, games=[bad, good])
        result = import_service.import_user_games(db_session, linked_player, max_games=10)
        assert result["inserted"] == 1
        assert result["skipped_invalid"] >= 1

    # IS_13 — missing id field is skipped.
    def test_import_skips_missing_id(self, db_session, linked_player, monkeypatch):
        no_id = {"pgn": _VALID_PGN, "winner": "white", "players": {}}
        _stub_games(monkeypatch, games=[no_id])
        result = import_service.import_user_games(db_session, linked_player, max_games=10)
        assert result["inserted"] == 0
        assert result["skipped_invalid"] == 1

    # IS_13 — oversize PGN is skipped.
    def test_import_skips_oversize_pgn(self, db_session, linked_player, monkeypatch):
        huge_pgn = _VALID_PGN + ("\n;" + "a" * import_service._MAX_PGN_BYTES)
        big = _game_dict(
            external_id="big",
            white="alice",
            black="bob",
            winner="white",
            pgn=huge_pgn,
        )
        _stub_games(monkeypatch, games=[big])
        result = import_service.import_user_games(db_session, linked_player, max_games=10)
        assert result["inserted"] == 0
        assert result["skipped_invalid"] == 1

    # IS_14 — watermark advances to the newest createdAt seen.
    def test_import_advances_watermark(self, db_session, linked_player, monkeypatch):
        newest = 1_700_000_002_000
        _stub_games(
            monkeypatch,
            games=[
                _game_dict(
                    external_id="new",
                    white="alice",
                    black="bob",
                    winner="white",
                    created_at_ms=newest,
                ),
                _game_dict(
                    external_id="old",
                    white="alice",
                    black="bob",
                    winner="black",
                    created_at_ms=1_700_000_000_000,
                ),
            ],
        )
        import_service.import_user_games(db_session, linked_player, max_games=10)
        link_row = (
            db_session.query(LinkedAccount)
            .filter(LinkedAccount.player_id == linked_player.id)
            .one()
        )
        assert link_row.last_imported_at is not None
        # Round-trip through the timezone-safe helper to avoid the
        # ``datetime.timestamp()`` local-time interpretation gotcha.
        assert import_service._naive_utc_to_ms(link_row.last_imported_at) == newest

    # IS_15
    def test_import_on_unlinked_player_raises(self, db_session, player, monkeypatch):
        _stub_games(monkeypatch, games=[])
        with pytest.raises(LichessNotLinkedError):
            import_service.import_user_games(db_session, player, max_games=10)

    # IS_15 — non-positive max rejected at the service layer.
    def test_import_rejects_non_positive_max(self, db_session, linked_player, monkeypatch):
        _stub_games(monkeypatch, games=[])
        with pytest.raises(ValueError):
            import_service.import_user_games(db_session, linked_player, max_games=0)

    # IS_12 — second call uses watermark as since_ms.
    def test_import_passes_watermark_as_since(self, db_session, linked_player, monkeypatch):
        # Seed one game so the watermark is set.
        _stub_games(
            monkeypatch,
            games=[
                _game_dict(
                    external_id="seed",
                    white="alice",
                    black="bob",
                    winner="white",
                    created_at_ms=1_700_000_005_000,
                )
            ],
        )
        import_service.import_user_games(db_session, linked_player, max_games=10)

        captured: dict = {}

        def _gen(username, **kwargs):
            captured["since_ms"] = kwargs.get("since_ms")
            return iter(())

        monkeypatch.setattr(import_service.lichess_client, "fetch_user_games", _gen)
        import_service.import_user_games(db_session, linked_player, max_games=10)
        assert captured["since_ms"] == 1_700_000_005_000


# ===========================================================================
# Pure-function: _derive_result
# ===========================================================================


class TestDeriveResult:
    # IS_16
    def test_white_winner_white_player(self):
        game = _game_dict(external_id="x", white="alice", black="bob", winner="white")
        assert import_service._derive_result(game, "alice") == "win"

    def test_white_winner_black_player(self):
        game = _game_dict(external_id="x", white="alice", black="bob", winner="white")
        assert import_service._derive_result(game, "bob") == "loss"

    def test_black_winner_white_player(self):
        game = _game_dict(external_id="x", white="alice", black="bob", winner="black")
        assert import_service._derive_result(game, "alice") == "loss"

    def test_no_winner_is_draw(self):
        game = _game_dict(external_id="x", white="alice", black="bob", winner=None)
        assert import_service._derive_result(game, "alice") == "draw"

    def test_missing_user_returns_none(self):
        game = _game_dict(external_id="x", white="alice", black="bob", winner="white")
        assert import_service._derive_result(game, "stranger") is None

    def test_unknown_winner_string_treated_as_draw(self):
        game = _game_dict(external_id="x", white="alice", black="bob", winner="purple")
        assert import_service._derive_result(game, "alice") == "draw"

    def test_ai_opponent_without_user_field(self):
        # Lichess shape for games vs Stockfish — no user on AI side.
        game = {
            "id": "ai1",
            "createdAt": 1,
            "winner": "white",
            "players": {
                "white": {"user": {"id": "alice", "name": "Alice"}, "rating": 1500},
                "black": {"aiLevel": 6},
            },
            "pgn": _VALID_PGN,
        }
        assert import_service._derive_result(game, "alice") == "win"


# ===========================================================================
# Pure-function: _derive_player_color (replay orientation)
# ===========================================================================


class TestDerivePlayerColor:
    # IS_17 — the side the linked user played, for replay board orientation.
    def test_user_is_white(self):
        game = _game_dict(external_id="x", white="alice", black="bob", winner="white")
        assert import_service._derive_player_color(game, "alice") == "white"

    def test_user_is_black(self):
        game = _game_dict(external_id="x", white="alice", black="bob", winner="white")
        assert import_service._derive_player_color(game, "bob") == "black"

    def test_user_not_a_player_returns_none(self):
        game = _game_dict(external_id="x", white="alice", black="bob", winner="white")
        assert import_service._derive_player_color(game, "stranger") is None


# ===========================================================================
# Router layer — direct handler invocation, slowapi disabled
# ===========================================================================


class TestRouter:
    # Valid PKCE verifier (matches CODE_VERIFIER_RE, 43-128 unreserved).
    _VALID_VERIFIER = "v" * 50

    @staticmethod
    def _stub_oauth(monkeypatch, *, account=None, exchange_exc=None, fetch_exc=None):
        """Stub the Lichess OAuth exchange / fetch / revoke that POST
        /lichess/link now performs (no more self-asserted username)."""

        def _exchange(code, code_verifier, redirect_uri=None):
            if exchange_exc is not None:
                raise exchange_exc
            return "tok-verified"

        def _fetch(token):
            if fetch_exc is not None:
                raise fetch_exc
            return account

        monkeypatch.setattr(lichess_client, "exchange_authorization_code", _exchange)
        monkeypatch.setattr(lichess_client, "fetch_account", _fetch)
        monkeypatch.setattr(lichess_client, "revoke_token", lambda t: None)

    # RT_01 — schema: link takes an OAuth code + PKCE verifier now.
    @pytest.mark.parametrize("bad_verifier", ["", " ", "short", "x" * 200])
    def test_link_request_rejects_malformed_verifier(self, bad_verifier):
        with pytest.raises(ValidationError):
            LinkRequest(code="authcode123", code_verifier=bad_verifier)

    def test_link_request_rejects_empty_code(self):
        with pytest.raises(ValidationError):
            LinkRequest(code="", code_verifier="v" * 50)

    def test_link_request_accepts_oauth_shape(self):
        req = LinkRequest(code="authcode123", code_verifier="v" * 50)
        assert req.code == "authcode123"

    # RT_02 — a failed OAuth authorization → 401 (no linking).
    def test_router_link_oauth_failure_to_401(self, db_session, player, monkeypatch):
        self._stub_oauth(monkeypatch, exchange_exc=lichess_client.LichessOAuthError("bad grant"))
        with _limiter_disabled():
            with pytest.raises(Exception) as excinfo:
                link(
                    request=_fake_request(),
                    req=LinkRequest(code="c", code_verifier=self._VALID_VERIFIER),
                    player=player,
                    db=db_session,
                )
        assert getattr(excinfo.value, "status_code", None) == 401

    # RT_02b — verified OAuth links the PROFILE identity (not a typed name).
    def test_router_link_oauth_links_verified_identity(self, db_session, player, monkeypatch):
        self._stub_oauth(monkeypatch, account={"id": "alice", "perfs": {}})
        with _limiter_disabled():
            result = link(
                request=_fake_request(),
                req=LinkRequest(code="c", code_verifier=self._VALID_VERIFIER),
                player=player,
                db=db_session,
            )
        assert result["external_username"] == "alice"
        assert (
            db_session.query(LinkedAccount)
            .filter_by(player_id=player.id, external_username="alice")
            .count()
            == 1
        )

    # RT_03 — verified OAuth ownership CLAIMS the handle from another
    # player's self-asserted link (no 409 — matches sign-in).
    def test_router_link_oauth_claims_from_other_player(
        self, db_session, player, other_player, monkeypatch
    ):
        import_service.link_account(
            db_session, other_player, "alice", profile={"id": "alice", "perfs": {}}
        )
        self._stub_oauth(monkeypatch, account={"id": "alice", "perfs": {}})
        with _limiter_disabled():
            result = link(
                request=_fake_request(),
                req=LinkRequest(code="c", code_verifier=self._VALID_VERIFIER),
                player=player,
                db=db_session,
            )
        assert result["external_username"] == "alice"
        rows = db_session.query(LinkedAccount).filter_by(external_username="alice").all()
        assert len(rows) == 1
        assert rows[0].player_id == player.id

    # RT_06 — Lichess rate-limiting the exchange → 503.
    def test_router_link_rate_limited_to_503(self, db_session, player, monkeypatch):
        self._stub_oauth(
            monkeypatch, exchange_exc=lichess_client.LichessRateLimited("slow", retry_after=30)
        )
        with _limiter_disabled():
            with pytest.raises(Exception) as excinfo:
                link(
                    request=_fake_request(),
                    req=LinkRequest(code="c", code_verifier=self._VALID_VERIFIER),
                    player=player,
                    db=db_session,
                )
        assert getattr(excinfo.value, "status_code", None) == 503

    # RT_06 — Lichess 5xx on the exchange → 502.
    def test_router_link_upstream_to_502(self, db_session, player, monkeypatch):
        self._stub_oauth(monkeypatch, exchange_exc=lichess_client.LichessUpstreamError("boom"))
        with _limiter_disabled():
            with pytest.raises(Exception) as excinfo:
                link(
                    request=_fake_request(),
                    req=LinkRequest(code="c", code_verifier=self._VALID_VERIFIER),
                    player=player,
                    db=db_session,
                )
        assert getattr(excinfo.value, "status_code", None) == 502

    # RT_04
    def test_router_status_unlinked(self, db_session, player):
        result = status(request=_fake_request(), player=player, db=db_session)
        assert result == {"linked": False}

    def test_router_status_linked(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(
            db_session, player, "alice", profile={"id": "alice", "perfs": {}}
        )
        result = status(request=_fake_request(), player=player, db=db_session)
        assert result["linked"] is True
        assert result["external_username"] == "alice"

    # RT_05
    def test_router_import_returns_counts(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(
            db_session, player, "alice", profile={"id": "alice", "perfs": {}}
        )
        _stub_games(
            monkeypatch,
            games=[
                _game_dict(external_id="g1", white="alice", black="bob", winner="white"),
            ],
        )
        with _limiter_disabled():
            result = trigger_import(
                request=_fake_request(),
                response=_fake_response(),
                player=player,
                db=db_session,
                max_games=10,
                rated=True,
            )
        assert result["inserted"] == 1

    # RT_06 — import on unlinked → 400.
    def test_router_import_unlinked_translates_to_400(self, db_session, player, monkeypatch):
        _stub_games(monkeypatch, games=[])
        with _limiter_disabled():
            with pytest.raises(Exception) as excinfo:
                trigger_import(
                    request=_fake_request(),
                    response=_fake_response(),
                    player=player,
                    db=db_session,
                    max_games=10,
                    rated=True,
                )
        assert getattr(excinfo.value, "status_code", None) == 400

    def test_router_unlink_returns_false_when_unlinked(self, db_session, player):
        with _limiter_disabled():
            result = unlink(request=_fake_request(), player=player, db=db_session)
        assert result == {"unlinked": False}

    def test_router_unlink_returns_true_after_link(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(
            db_session, player, "alice", profile={"id": "alice", "perfs": {}}
        )
        with _limiter_disabled():
            result = unlink(request=_fake_request(), player=player, db=db_session)
        assert result == {"unlinked": True}


# ===========================================================================
# v2 async import — job lifecycle, coalesce, cancellation, janitor, v1 pin
# ===========================================================================
#
# IJ_01  start_import_job creates a queued row + commits.
# IJ_02  start_import_job coalesces concurrent callers (same player_id)
#        onto a single row — pinned by a real two-thread test.
# IJ_03  start_import_job rejects an unlinked player with LichessNotLinkedError.
# IJ_04  run_import_job promotes queued -> running -> succeeded on a clean stream.
# IJ_05  run_import_job marks the row failed on an exception, does NOT advance
#        the LinkedAccount watermark.
# IJ_06  run_import_job is idempotent at pickup: a row that's already terminal
#        (set by unlink_account, the janitor, or a prior worker) is skipped
#        without clobbering error_message.
# IJ_07  unlink_account marks any active job as failed before deleting the link.
# IJ_08  _run_import_stream observes an external job.status flip mid-stream
#        and exits without advancing the watermark.
# IJ_09  cleanup_stale_import_jobs_on_startup sweeps queued/running rows.
# IJ_10  serialize_job exposes the v2 wire shape stably.
# IJ_11  POST /lichess/import without an X-API-Version header returns the
#        legacy v1 200 body shape verbatim (regression pin).
# IJ_12  POST /lichess/import with X-API-Version: 2 returns the v2 202 body
#        and dispatches a worker for queued rows.

import threading
import time

from sqlalchemy.pool import NullPool

from llm.seca.lichess import _locks_guard, _player_import_locks
from llm.seca.lichess.models import (
    JOB_STATUS_ACTIVE,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    LichessImportJob,
)


@pytest.fixture()
def cleared_player_locks():
    """Reset the per-player import lock dict between tests.

    The lock factory is process-global; previous tests may have stashed
    Locks keyed on player_ids reused here.  Clearing is fast and
    purely additive — production code never depends on the previous
    state of the dict.
    """
    with _locks_guard:
        _player_import_locks.clear()
    yield
    with _locks_guard:
        _player_import_locks.clear()


@pytest.fixture()
def worker_session_factory(db_session, monkeypatch):
    """Bind ``import_service._WorkerSession`` to the test's in-memory engine.

    Required for any test that exercises ``run_import_job`` or
    ``cleanup_stale_import_jobs_on_startup`` — those open their own
    session via the module-level factory, which would otherwise hit the
    production file-based SQLite.
    """
    engine = db_session.get_bind()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(import_service, "_WorkerSession", factory)
    return factory


class TestImportJobLifecycle:
    """IJ_01..IJ_06 + IJ_10 — start_import_job + run_import_job + serialize."""

    @pytest.fixture()
    def linked_player(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, player, "alice")
        return player

    # IJ_01
    def test_start_import_job_creates_queued_row(
        self, db_session, linked_player, cleared_player_locks
    ):
        job = import_service.start_import_job(db_session, linked_player, max_games=42)
        assert job.id  # UUID assigned
        assert job.status == JOB_STATUS_QUEUED
        assert job.target_max_games == 42
        assert job.inserted == 0
        assert job.skipped_duplicate == 0
        assert job.skipped_invalid == 0
        assert job.player_id == linked_player.id

        rows = db_session.query(LichessImportJob).filter_by(player_id=linked_player.id).all()
        assert len(rows) == 1

    # IJ_01 (negative: invalid max_games)
    def test_start_import_job_rejects_non_positive(
        self, db_session, linked_player, cleared_player_locks
    ):
        with pytest.raises(ValueError):
            import_service.start_import_job(db_session, linked_player, max_games=0)

    # IJ_03
    def test_start_import_job_raises_when_unlinked(self, db_session, player, cleared_player_locks):
        with pytest.raises(LichessNotLinkedError):
            import_service.start_import_job(db_session, player, max_games=10)

    # IJ_04
    def test_run_import_job_clean_stream_marks_succeeded(
        self,
        db_session,
        linked_player,
        monkeypatch,
        cleared_player_locks,
        worker_session_factory,
    ):
        _stub_games(
            monkeypatch,
            games=[
                _game_dict(external_id="g1", white="alice", black="bob", winner="white"),
                _game_dict(external_id="g2", white="alice", black="bob", winner="black"),
            ],
        )
        job = import_service.start_import_job(db_session, linked_player, max_games=50)
        # Worker runs synchronously from the test thread.
        import_service.run_import_job(job.id, max_games=50, rated=True)

        db_session.refresh(job)
        assert job.status == JOB_STATUS_SUCCEEDED
        assert job.inserted == 2
        assert job.error_message is None

        # Watermark advanced on success.
        link_row = (
            db_session.query(LinkedAccount)
            .filter(LinkedAccount.player_id == linked_player.id)
            .one()
        )
        assert link_row.last_imported_at is not None

    # IJ_05
    def test_run_import_job_failure_preserves_watermark(
        self,
        db_session,
        linked_player,
        monkeypatch,
        cleared_player_locks,
        worker_session_factory,
    ):
        def _raising_iter(*_args, **_kwargs):
            yield _game_dict(external_id="g1", white="alice", black="bob", winner="white")
            raise lichess_client.LichessUpstreamError("simulated upstream failure")

        monkeypatch.setattr(import_service.lichess_client, "fetch_user_games", _raising_iter)

        job = import_service.start_import_job(db_session, linked_player, max_games=50)
        import_service.run_import_job(job.id, max_games=50, rated=True)

        db_session.refresh(job)
        assert job.status == JOB_STATUS_FAILED
        assert job.error_message is not None
        assert "simulated upstream failure" in job.error_message

        # Watermark stays None — the partial commits did NOT advance it.
        link_row = (
            db_session.query(LinkedAccount)
            .filter(LinkedAccount.player_id == linked_player.id)
            .one()
        )
        assert link_row.last_imported_at is None

    # IJ_06
    def test_run_import_job_skips_already_terminal_row(
        self,
        db_session,
        linked_player,
        cleared_player_locks,
        worker_session_factory,
    ):
        # Insert a row already marked failed (e.g. by unlink_account).
        # The worker must NOT overwrite the error_message with a generic
        # "linked account not found" or similar.
        job = LichessImportJob(
            player_id=linked_player.id,
            status=JOB_STATUS_FAILED,
            target_max_games=10,
            error_message="link removed during import",
        )
        db_session.add(job)
        db_session.commit()

        import_service.run_import_job(job.id, max_games=10, rated=True)

        db_session.refresh(job)
        assert job.status == JOB_STATUS_FAILED
        assert job.error_message == "link removed during import"

    # IJ_10
    def test_serialize_job_shape_stable(self, db_session, linked_player, cleared_player_locks):
        job = import_service.start_import_job(db_session, linked_player, max_games=25)
        payload = import_service.serialize_job(job)
        assert set(payload.keys()) == {
            "job_id",
            "status",
            "inserted",
            "skipped_duplicate",
            "skipped_invalid",
            # Post-import engine-analysis counter, added 2026-07-03
            # (docs/API_CONTRACTS.md §31) — additive field, deployed
            # clients decode with ignoreUnknownKeys.
            "analyzed",
            "target_max_games",
            "last_imported_at_ms",
            "error_message",
            "created_at",
            "updated_at",
        }
        assert payload["job_id"] == job.id
        assert payload["status"] == JOB_STATUS_QUEUED
        assert payload["target_max_games"] == 25
        assert payload["inserted"] == 0
        assert payload["analyzed"] == 0
        assert payload["last_imported_at_ms"] is None


class TestCoalesce:
    """IJ_02 — real two-thread race against the per-player lock."""

    def test_coalesce_concurrent_starts_same_player(self, cleared_player_locks, tmp_path):
        # A real on-disk temp DB with per-connection pooling — NOT
        # ``:memory:`` + StaticPool.  The race under test is the
        # APPLICATION-level per-player lock inside ``start_import_job``
        # (``get_player_import_lock``), but StaticPool hands both worker
        # threads the SAME ``sqlite3`` connection object, adding a
        # DRIVER-level race the lock never claimed to cover: under CI
        # load the interleaved cursor use threw
        # ``InterfaceError('bad parameter or other API misuse')`` in one
        # worker and ``BrokenBarrierError`` in the other (main run
        # 28672672287, 2026-07-03) even though the coalesce logic was
        # correct.  NullPool gives every session its own connection to
        # the shared file, so the only concurrency left is exactly the
        # one this test exists to exercise; sqlite3's default 5 s busy
        # timeout absorbs the brief window where both connections write.
        engine = create_engine(
            "sqlite:///" + (tmp_path / "coalesce.db").as_posix(),
            poolclass=NullPool,
        )
        Base.metadata.create_all(bind=engine)
        SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)

        with SessionFactory() as setup_db:
            setup_player = Player(
                email="coalesce@test.com",
                password_hash="x",
                rating=1200.0,
                confidence=0.5,
                skill_vector_json="{}",
                player_embedding="[]",
            )
            setup_db.add(setup_player)
            setup_db.flush()
            setup_db.add(
                LinkedAccount(
                    player_id=setup_player.id,
                    platform="lichess",
                    external_username="alice",
                )
            )
            setup_db.commit()
            player_id = setup_player.id

        results: list[str] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                with SessionFactory() as db:
                    p = db.get(Player, player_id)
                    barrier.wait(timeout=5)
                    job = import_service.start_import_job(db, p, max_games=25)
                    results.append(job.id)
            except BaseException as exc:  # pylint: disable=broad-except
                errors.append(exc)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"worker threw: {errors}"
        assert len(results) == 2, f"expected both threads to return; got {results}"
        assert results[0] == results[1], "coalesce must hand back the same job_id"

        with SessionFactory() as db:
            n = db.query(LichessImportJob).filter_by(player_id=player_id).count()
            assert n == 1, f"expected exactly 1 row, got {n}"

        # NullPool closes connections on session close, but dispose
        # explicitly so the tmp file is releasable on Windows runs.
        engine.dispose()


class TestUnlinkTerminatesRunning:
    """IJ_07 + IJ_08 — unlink + mid-stream cancellation."""

    @pytest.fixture()
    def linked_player(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, player, "alice")
        return player

    # IJ_07
    def test_unlink_marks_active_jobs_failed(self, db_session, linked_player, cleared_player_locks):
        running = LichessImportJob(
            player_id=linked_player.id,
            status=JOB_STATUS_RUNNING,
            target_max_games=50,
            inserted=3,
        )
        queued = LichessImportJob(
            player_id=linked_player.id,
            status=JOB_STATUS_QUEUED,
            target_max_games=50,
        )
        succeeded = LichessImportJob(
            player_id=linked_player.id,
            status=JOB_STATUS_SUCCEEDED,
            target_max_games=50,
            inserted=10,
        )
        db_session.add_all([running, queued, succeeded])
        db_session.commit()

        import_service.unlink_account(db_session, linked_player)

        db_session.refresh(running)
        db_session.refresh(queued)
        db_session.refresh(succeeded)
        assert running.status == JOB_STATUS_FAILED
        assert running.error_message == "link removed during import"
        assert queued.status == JOB_STATUS_FAILED
        assert queued.error_message == "link removed during import"
        # Already-terminal rows must NOT be touched.
        assert succeeded.status == JOB_STATUS_SUCCEEDED
        assert succeeded.error_message is None
        # And the link row itself is gone.
        assert db_session.query(LinkedAccount).filter_by(player_id=linked_player.id).first() is None

    # IJ_08
    def test_run_import_stream_aborts_on_external_status_flip(
        self,
        db_session,
        linked_player,
        monkeypatch,
        cleared_player_locks,
    ):
        job = LichessImportJob(
            player_id=linked_player.id,
            status=JOB_STATUS_RUNNING,
            target_max_games=10,
        )
        db_session.add(job)
        db_session.commit()

        games = [
            _game_dict(
                external_id=f"g{i}",
                white="alice",
                black="bob",
                winner="white",
                created_at_ms=1_700_000_000_000 + i * 1000,
            )
            for i in range(5)
        ]

        yielded_count = [0]

        def _cancelling_iter(*_args, **_kwargs):
            for i, g in enumerate(games):
                yielded_count[0] = i + 1
                if i == 1:
                    # After the SECOND yield, externally flip the job
                    # to failed (simulating ``unlink_account`` running
                    # in another request thread).  ``_run_import_stream``
                    # commits per game then refreshes ``job.status``;
                    # the flip will be observed on the next iteration.
                    db_session.query(LichessImportJob).filter_by(id=job.id).update(
                        {
                            "status": JOB_STATUS_FAILED,
                            "error_message": "link removed during import",
                        }
                    )
                    db_session.commit()
                yield g

        monkeypatch.setattr(import_service.lichess_client, "fetch_user_games", _cancelling_iter)

        link_row = db_session.query(LinkedAccount).filter_by(player_id=linked_player.id).one()
        import_service._run_import_stream(
            db_session,
            link_row,
            linked_player,
            max_games=10,
            rated=True,
            job=job,
        )

        # At least one game past the flip-trigger may land before the
        # next-iteration refresh sees the failed status — but the stream
        # must NOT process all 5.
        inserted = (
            db_session.query(GameEvent)
            .filter_by(player_id=linked_player.id, source="lichess")
            .count()
        )
        assert inserted < 5, f"expected partial insertion; got {inserted}"
        # Status check must have stopped the iterator before exhaustion.
        assert yielded_count[0] < 5

        # Cancellation must NOT advance the watermark.
        db_session.refresh(link_row)
        assert link_row.last_imported_at is None


class TestStartupJanitor:
    """IJ_09 — cleanup_stale_import_jobs_on_startup."""

    def test_janitor_sweeps_active_rows_to_failed(
        self, db_session, player, cleared_player_locks, worker_session_factory
    ):
        running = LichessImportJob(
            player_id=player.id,
            status=JOB_STATUS_RUNNING,
            target_max_games=10,
        )
        queued = LichessImportJob(
            player_id=player.id,
            status=JOB_STATUS_QUEUED,
            target_max_games=10,
        )
        succeeded = LichessImportJob(
            player_id=player.id,
            status=JOB_STATUS_SUCCEEDED,
            target_max_games=10,
            inserted=7,
        )
        db_session.add_all([running, queued, succeeded])
        db_session.commit()

        affected = import_service.cleanup_stale_import_jobs_on_startup()
        assert affected == 2

        db_session.expire_all()
        running = db_session.get(LichessImportJob, running.id)
        queued = db_session.get(LichessImportJob, queued.id)
        succeeded = db_session.get(LichessImportJob, succeeded.id)
        assert running.status == JOB_STATUS_FAILED
        assert running.error_message == "abandoned by server restart"
        assert queued.status == JOB_STATUS_FAILED
        assert queued.error_message == "abandoned by server restart"
        # Already-terminal rows must stay untouched.
        assert succeeded.status == JOB_STATUS_SUCCEEDED
        assert succeeded.error_message is None

    def test_janitor_is_idempotent(
        self, db_session, player, cleared_player_locks, worker_session_factory
    ):
        # Second call (after the first sweep) is a no-op.
        running = LichessImportJob(
            player_id=player.id,
            status=JOB_STATUS_RUNNING,
            target_max_games=10,
        )
        db_session.add(running)
        db_session.commit()

        assert import_service.cleanup_stale_import_jobs_on_startup() == 1
        assert import_service.cleanup_stale_import_jobs_on_startup() == 0


class TestV1LegacyPathRegression:
    """IJ_11 — pin the v1 sync response shape so it never silently drifts.

    The shipped Android v1 client (pre-PR #187 + before the Lichess v2
    PR) sends ``X-API-Version: 1`` (or no header — also routed to v1).
    Its parser expects exactly the keys
    ``{"inserted", "skipped_duplicate", "skipped_invalid", "last_imported_at"}``.
    A drift here would 500 every shipped client until they update.
    """

    @pytest.fixture()
    def linked_player(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, player, "alice")
        return player

    def _request_with_version(self, version: str | None) -> StarletteRequest:
        headers = []
        if version is not None:
            headers.append((b"x-api-version", version.encode()))
        return _fake_request(headers=headers)

    def test_no_header_returns_v1_shape(self, db_session, linked_player, monkeypatch):
        _stub_games(
            monkeypatch,
            games=[_game_dict(external_id="g1", white="alice", black="bob", winner="white")],
        )
        with _limiter_disabled():
            result = trigger_import(
                request=self._request_with_version(None),
                response=_fake_response(),
                player=linked_player,
                db=db_session,
                max_games=10,
                rated=True,
            )

        # v1 contract: exactly these keys, no job_id / no status / no target_max_games.
        assert isinstance(result, dict)
        assert set(result.keys()) == {
            "inserted",
            "skipped_duplicate",
            "skipped_invalid",
            "last_imported_at",
        }
        assert "job_id" not in result
        assert "status" not in result
        assert result["inserted"] == 1

    def test_explicit_v1_header_returns_v1_shape(self, db_session, linked_player, monkeypatch):
        _stub_games(
            monkeypatch,
            games=[_game_dict(external_id="g1", white="alice", black="bob", winner="white")],
        )
        with _limiter_disabled():
            result = trigger_import(
                request=self._request_with_version("1"),
                response=_fake_response(),
                player=linked_player,
                db=db_session,
                max_games=10,
                rated=True,
            )
        assert set(result.keys()) == {
            "inserted",
            "skipped_duplicate",
            "skipped_invalid",
            "last_imported_at",
        }
        assert result["inserted"] == 1

    # IJ_12 — v2 header returns the new shape + 202 status.
    def test_v2_header_returns_job_payload(
        self,
        db_session,
        linked_player,
        monkeypatch,
        cleared_player_locks,
    ):
        # Stub the worker pool so the test stays single-threaded: the
        # router will submit() the job, but we just want to verify the
        # 202 + job_id response shape, not the worker's progress.
        submitted: list[tuple] = []

        class _StubExecutor:
            def submit(self, fn, *args, **kwargs):  # pylint: disable=unused-argument
                submitted.append((fn, args, kwargs))

        monkeypatch.setattr("llm.seca.lichess.router._executor", _StubExecutor())

        response = _fake_response()
        with _limiter_disabled():
            result = trigger_import(
                request=self._request_with_version("2"),
                response=response,
                player=linked_player,
                db=db_session,
                max_games=25,
                rated=True,
            )

        assert response.status_code == 202
        # v2 shape — must carry job_id + target_max_games.
        assert set(result.keys()) >= {
            "job_id",
            "status",
            "inserted",
            "skipped_duplicate",
            "skipped_invalid",
            "target_max_games",
        }
        assert result["target_max_games"] == 25
        assert result["inserted"] == 0
        # Worker submission fired for the queued (fresh-inserted) row.
        assert len(submitted) == 1
        assert submitted[0][1][0] == result["job_id"]


class TestGetImportJobRoute:
    """Owner-scoped GET /lichess/import/job/{job_id}."""

    @pytest.fixture()
    def linked_player(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, player, "alice")
        return player

    def test_get_import_job_returns_owned_job(
        self, db_session, linked_player, cleared_player_locks
    ):
        from llm.seca.lichess.router import get_import_job

        job = import_service.start_import_job(db_session, linked_player, max_games=10)
        with _limiter_disabled():
            payload = get_import_job(
                request=_fake_request(),
                job_id=job.id,
                player=linked_player,
                db=db_session,
            )
        assert payload["job_id"] == job.id
        assert payload["status"] == JOB_STATUS_QUEUED

    def test_get_import_job_404_for_nonexistent(self, db_session, linked_player):
        from fastapi import HTTPException

        from llm.seca.lichess.router import get_import_job

        with _limiter_disabled(), pytest.raises(HTTPException) as excinfo:
            get_import_job(
                request=_fake_request(),
                job_id="no-such-job",
                player=linked_player,
                db=db_session,
            )
        assert excinfo.value.status_code == 404

    def test_get_import_job_404_for_other_players_job(
        self, db_session, linked_player, other_player, cleared_player_locks
    ):
        from fastapi import HTTPException

        from llm.seca.lichess.router import get_import_job

        # Job owned by linked_player; other_player must not be able to read it.
        job = import_service.start_import_job(db_session, linked_player, max_games=10)
        with _limiter_disabled(), pytest.raises(HTTPException) as excinfo:
            get_import_job(
                request=_fake_request(),
                job_id=job.id,
                player=other_player,
                db=db_session,
            )
        assert excinfo.value.status_code == 404


# ===========================================================================
# Disconnect detection + reconnect resolution (communication & access
# spec §2.6, adapted — see LinkedAccount.disconnected_at)
# ===========================================================================
#
# DC_01  Worker 404 → link marked disconnected + reconnect system_alert
#        raised + job failed; no game_analyzed row for a failed job.
# DC_02  Repeat 404 keeps ONE live alert (dedup) and the ORIGINAL
#        disconnected_at stamp.
# DC_03  Clean import afterwards clears the flag and dismisses the alert.
# DC_04  Job success with analyzed>0 raises the game_analyzed feed entry
#        (batched copy comes from the producer's own tests).
# DC_05  unlink_account dismisses the live alert.
# DC_06  Re-linking dismisses the live alert; fresh link reads connected.
# DC_07  get_status exposes disconnected / disconnected_at.
# DC_08  Sync v1 import_user_games 404 also marks + alerts, then
#        re-raises for the router's existing 404 translation.


from llm.seca.notifications.models import (
    ACTION_LICHESS_RECONNECT,
    Notification,
    PRIORITY_HIGH,
    TYPE_GAME_ANALYZED,
    TYPE_SYSTEM_ALERT,
)


def _stub_games_user_not_found(monkeypatch):
    """fetch_user_games stand-in whose stream 404s on first pull."""

    def _gen(username, **kwargs):
        raise lichess_client.LichessUserNotFound("user not found")
        yield  # pylint: disable=unreachable  # generator shape, like the real client

    monkeypatch.setattr(import_service.lichess_client, "fetch_user_games", _gen)


def _live_reconnect_alerts(db_session, player_id):
    return (
        db_session.query(Notification)
        .filter(
            Notification.player_id == player_id,
            Notification.type == TYPE_SYSTEM_ALERT,
            Notification.action == ACTION_LICHESS_RECONNECT,
            Notification.dismissed_at.is_(None),
        )
        .all()
    )


class TestDisconnectReconnect:
    @pytest.fixture()
    def linked_player(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        import_service.link_account(db_session, player, "alice")
        return player

    def _fail_one_job(self, db_session, linked_player, monkeypatch):
        _stub_games_user_not_found(monkeypatch)
        job = import_service.start_import_job(db_session, linked_player, max_games=50)
        import_service.run_import_job(job.id, max_games=50, rated=True)
        db_session.expire_all()
        return job

    # DC_01
    def test_worker_404_marks_disconnected_and_alerts(
        self, db_session, linked_player, monkeypatch, cleared_player_locks, worker_session_factory
    ):
        job = self._fail_one_job(db_session, linked_player, monkeypatch)
        assert job.status == JOB_STATUS_FAILED

        link_row = (
            db_session.query(LinkedAccount)
            .filter(LinkedAccount.player_id == linked_player.id)
            .one()
        )
        assert link_row.disconnected_at is not None

        alerts = _live_reconnect_alerts(db_session, linked_player.id)
        assert len(alerts) == 1
        assert alerts[0].priority == PRIORITY_HIGH
        assert alerts[0].expires_at is None  # "until resolved"
        assert alerts[0].action_label == "Reconnect"
        assert "alice" in alerts[0].body

        # A failed job must not leave a "games reviewed" entry behind.
        analyzed_rows = (
            db_session.query(Notification)
            .filter(
                Notification.player_id == linked_player.id,
                Notification.type == TYPE_GAME_ANALYZED,
            )
            .all()
        )
        assert analyzed_rows == []

    # DC_02
    def test_repeat_404_keeps_single_alert_and_first_stamp(
        self, db_session, linked_player, monkeypatch, cleared_player_locks, worker_session_factory
    ):
        self._fail_one_job(db_session, linked_player, monkeypatch)
        link_row = (
            db_session.query(LinkedAccount)
            .filter(LinkedAccount.player_id == linked_player.id)
            .one()
        )
        first_stamp = link_row.disconnected_at

        self._fail_one_job(db_session, linked_player, monkeypatch)
        db_session.refresh(link_row)
        assert link_row.disconnected_at == first_stamp
        assert len(_live_reconnect_alerts(db_session, linked_player.id)) == 1

    # DC_03
    def test_clean_import_clears_flag_and_dismisses_alert(
        self, db_session, linked_player, monkeypatch, cleared_player_locks, worker_session_factory
    ):
        self._fail_one_job(db_session, linked_player, monkeypatch)

        _stub_games(
            monkeypatch,
            games=[_game_dict(external_id="g9", white="alice", black="bob", winner="white")],
        )
        job = import_service.start_import_job(db_session, linked_player, max_games=50)
        import_service.run_import_job(job.id, max_games=50, rated=True)
        db_session.expire_all()

        link_row = (
            db_session.query(LinkedAccount)
            .filter(LinkedAccount.player_id == linked_player.id)
            .one()
        )
        assert link_row.disconnected_at is None
        assert _live_reconnect_alerts(db_session, linked_player.id) == []

    # DC_04
    def test_success_with_analyzed_games_raises_feed_entry(
        self, db_session, linked_player, monkeypatch, cleared_player_locks, worker_session_factory
    ):
        from llm.seca.lichess import analysis_service

        _stub_games(
            monkeypatch,
            games=[
                _game_dict(external_id="a1", white="alice", black="bob", winner="white"),
                _game_dict(external_id="a2", white="alice", black="bob", winner="black"),
            ],
        )

        def _fake_analyze(db, player, pool, job=None):
            job.analyzed = 2
            db.commit()

        monkeypatch.setattr(analysis_service, "analyze_unscored_games", _fake_analyze)

        job = import_service.start_import_job(db_session, linked_player, max_games=50)
        import_service.run_import_job(job.id, max_games=50, rated=True, engine_pool=object())
        db_session.expire_all()

        rows = (
            db_session.query(Notification)
            .filter(
                Notification.player_id == linked_player.id,
                Notification.type == TYPE_GAME_ANALYZED,
            )
            .all()
        )
        assert len(rows) == 1
        assert json.loads(rows[0].metadata_json) == {"games_analyzed": 2}
        assert rows[0].title == "2 games reviewed"

    # DC_05
    def test_unlink_dismisses_alert(
        self, db_session, linked_player, monkeypatch, cleared_player_locks, worker_session_factory
    ):
        self._fail_one_job(db_session, linked_player, monkeypatch)
        assert len(_live_reconnect_alerts(db_session, linked_player.id)) == 1

        assert import_service.unlink_account(db_session, linked_player) is True
        assert _live_reconnect_alerts(db_session, linked_player.id) == []

    # DC_06
    def test_relink_dismisses_alert_and_reads_connected(
        self, db_session, linked_player, monkeypatch, cleared_player_locks, worker_session_factory
    ):
        self._fail_one_job(db_session, linked_player, monkeypatch)

        _stub_profile(monkeypatch, profile={"id": "alice2", "perfs": {}})
        import_service.link_account(db_session, linked_player, "alice2")

        assert _live_reconnect_alerts(db_session, linked_player.id) == []
        link_row = (
            db_session.query(LinkedAccount)
            .filter(LinkedAccount.player_id == linked_player.id)
            .one()
        )
        assert link_row.disconnected_at is None

    # DC_07
    def test_get_status_exposes_disconnected_fields(
        self, db_session, linked_player, monkeypatch, cleared_player_locks, worker_session_factory
    ):
        fresh = import_service.get_status(db_session, linked_player)
        assert fresh["disconnected"] is False
        assert fresh["disconnected_at"] is None

        self._fail_one_job(db_session, linked_player, monkeypatch)
        broken = import_service.get_status(db_session, linked_player)
        assert broken["disconnected"] is True
        assert isinstance(broken["disconnected_at"], str)

    # DC_08
    def test_sync_import_404_marks_and_reraises(
        self, db_session, linked_player, monkeypatch, cleared_player_locks
    ):
        _stub_games_user_not_found(monkeypatch)
        with pytest.raises(lichess_client.LichessUserNotFound):
            import_service.import_user_games(db_session, linked_player, max_games=10)

        link_row = (
            db_session.query(LinkedAccount)
            .filter(LinkedAccount.player_id == linked_player.id)
            .one()
        )
        assert link_row.disconnected_at is not None
        assert len(_live_reconnect_alerts(db_session, linked_player.id)) == 1
