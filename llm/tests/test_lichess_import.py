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

RT_01  POST /lichess/link rejects malformed usernames at the schema layer.
RT_02  POST /lichess/link 404 propagates LichessUserNotFound.
RT_03  POST /lichess/link 409 on cross-player conflict.
RT_04  GET /lichess/status mirrors service output.
RT_05  POST /lichess/import returns counts.
RT_06  Service-layer errors map to documented HTTP codes.
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


def _fake_request() -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/lichess/link",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
    )


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
        # g1: alice was white, white won → win.
        assert next(r for r in rows if r.external_game_id == "g1").result == "win"
        # g2: alice was black, white won → loss.
        assert next(r for r in rows if r.external_game_id == "g2").result == "loss"

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
# Router layer — direct handler invocation, slowapi disabled
# ===========================================================================


class TestRouter:
    # RT_01
    @pytest.mark.parametrize(
        "bad",
        [
            "",
            " ",
            "a",  # too short
            "x" * 31,  # too long
            "with spaces",
            "ümlaut",
            "rot/13",
            "drop;table",
        ],
    )
    def test_link_request_rejects_malformed_username(self, bad):
        with pytest.raises(ValidationError):
            LinkRequest(username=bad)

    @pytest.mark.parametrize("good", ["DrNykterstein", "alice", "user_42", "a-b"])
    def test_link_request_accepts_valid_username(self, good):
        assert LinkRequest(username=good).username == good

    # RT_02
    def test_router_link_translates_user_not_found_to_404(self, db_session, player, monkeypatch):
        def _raise(username):
            raise lichess_client.LichessUserNotFound("nope")

        monkeypatch.setattr(import_service.lichess_client, "fetch_user_profile", _raise)
        req = LinkRequest(username="ghost")
        with _limiter_disabled():
            with pytest.raises(Exception) as excinfo:
                link(request=_fake_request(), req=req, player=player, db=db_session)
        assert getattr(excinfo.value, "status_code", None) == 404

    # RT_03
    def test_router_link_translates_already_linked_to_409(
        self, db_session, player, other_player, monkeypatch
    ):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        with _limiter_disabled():
            link(
                request=_fake_request(),
                req=LinkRequest(username="alice"),
                player=other_player,
                db=db_session,
            )
        with _limiter_disabled():
            with pytest.raises(Exception) as excinfo:
                link(
                    request=_fake_request(),
                    req=LinkRequest(username="alice"),
                    player=player,
                    db=db_session,
                )
        assert getattr(excinfo.value, "status_code", None) == 409

    # RT_06 — rate-limited maps to 503 with Retry-After.
    def test_router_link_translates_rate_limited_to_503(self, db_session, player, monkeypatch):
        def _raise(username):
            raise lichess_client.LichessRateLimited("slow down", retry_after=30)

        monkeypatch.setattr(import_service.lichess_client, "fetch_user_profile", _raise)
        with _limiter_disabled():
            with pytest.raises(Exception) as excinfo:
                link(
                    request=_fake_request(),
                    req=LinkRequest(username="alice"),
                    player=player,
                    db=db_session,
                )
        exc = excinfo.value
        assert getattr(exc, "status_code", None) == 503
        assert (exc.headers or {}).get("Retry-After") == "30"

    # RT_06 — 5xx → 502.
    def test_router_link_translates_upstream_to_502(self, db_session, player, monkeypatch):
        def _raise(username):
            raise lichess_client.LichessUpstreamError("boom")

        monkeypatch.setattr(import_service.lichess_client, "fetch_user_profile", _raise)
        with _limiter_disabled():
            with pytest.raises(Exception) as excinfo:
                link(
                    request=_fake_request(),
                    req=LinkRequest(username="alice"),
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
        with _limiter_disabled():
            link(
                request=_fake_request(),
                req=LinkRequest(username="alice"),
                player=player,
                db=db_session,
            )
        result = status(request=_fake_request(), player=player, db=db_session)
        assert result["linked"] is True
        assert result["external_username"] == "alice"

    # RT_05
    def test_router_import_returns_counts(self, db_session, player, monkeypatch):
        _stub_profile(monkeypatch, profile={"id": "alice", "perfs": {}})
        with _limiter_disabled():
            link(
                request=_fake_request(),
                req=LinkRequest(username="alice"),
                player=player,
                db=db_session,
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
        with _limiter_disabled():
            link(
                request=_fake_request(),
                req=LinkRequest(username="alice"),
                player=player,
                db=db_session,
            )
            result = unlink(request=_fake_request(), player=player, db=db_session)
        assert result == {"unlinked": True}
