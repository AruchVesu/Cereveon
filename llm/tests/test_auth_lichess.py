"""Backend tests for POST /auth/lichess — "Sign in with Lichess" (OAuth PKCE).

Covers three layers:

* ``llm.seca.lichess.client``  — the OAuth additions: authorization-code
                                 exchange, account fetch, token revocation,
                                 hostile-input rejection.
* ``llm.seca.auth.service``    — ``login_with_lichess`` find-or-create +
                                 unusable-credential semantics.
* ``llm.seca.auth.router``     — request-schema validation, exception → HTTP
                                 translation, auto-link behaviour.

Lichess HTTP I/O is mocked at the function level (router tests) or via a
fake ``httpx.Client`` (client tests) — deterministic, no network.  Router
handlers are called directly with the limiter disabled, matching the
pattern in ``test_lichess_import.py`` / ``test_auth_update_me.py``.

Pinned invariants
-----------------
OA_01  exchange_authorization_code posts the full PKCE grant form and
       returns the access token.
OA_02  Grant rejection (4xx from Lichess) → LichessOAuthError.
OA_03  5xx → LichessUpstreamError; 429 → LichessRateLimited.
OA_04  Malformed code / code_verifier rejected BEFORE any network call.
OA_05  fetch_account validates the ``id`` shape fail-closed
       (LichessParseError on traversal / control / oversize ids).
OA_06  fetch_account maps 401 → LichessOAuthError.
OA_07  revoke_token never raises, even on transport failure.

LI_01  First sign-in creates a player: lichess_user_id set, synthetic
       ``lichess:<id>`` email, created=True, token + player_id returned.
LI_02  Second sign-in reuses the same player (created=False, no dup row).
LI_03  OAuth grant rejection → HTTP 401.
LI_04  Lichess rate limit → 503; upstream / parse failure → 502.
LI_05  Schema rejects a non-RFC-7636 code_verifier.
LI_06  Schema rejects control characters in the authorization code.
LI_07  Auto-link creates the LinkedAccount row + first-link calibration.
LI_08  Cross-player link conflict: sign-in still succeeds, the existing
       owner keeps the link.
LI_09  An existing link (even to a different handle) is never clobbered.
LI_10  The synthetic email shape is rejected by the /auth/register email
       validator — the lichess: namespace cannot be squatted.
LI_11  Password login can never match a lichess-created account.
LI_12  The issued token round-trips through get_player_by_session.
LI_13  Sign-in succeeds even when auto-link raises (best-effort contract).
"""

from __future__ import annotations

import hashlib
import os

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from contextlib import contextmanager

from fastapi import HTTPException
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
from llm.seca.auth.router import LichessLoginRequest, RegisterRequest, login_lichess
from llm.seca.auth.service import AuthService
from llm.seca.auth.tokens import decode_token
from llm.seca.lichess import client as lichess_client
from llm.seca.lichess.import_service import PLATFORM_LICHESS
from llm.seca.lichess.models import LinkedAccount
from llm.seca.shared_limiter import limiter

# ---------------------------------------------------------------------------
# Shared fixtures + helpers
# ---------------------------------------------------------------------------

# A structurally-valid RFC 7636 verifier (43 chars of the unreserved set).
VALID_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
VALID_CODE = "auth-code-abc123"

# Minimal /api/account payload: canonical id + display username + a rapid
# perf so first-link calibration has something to work with.
ACCOUNT_JSON = {
    "id": "chesswizard",
    "username": "ChessWizard",
    "perfs": {"rapid": {"games": 120, "rating": 1907, "prov": False}},
}


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


def _fake_request() -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/lichess",
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


def _patch_oauth_success(monkeypatch, account: dict | None = None):
    """Patch the two Lichess round-trips to a deterministic success."""
    monkeypatch.setattr(
        lichess_client, "exchange_authorization_code", lambda code, verifier: "lio_testtoken"
    )
    monkeypatch.setattr(
        lichess_client, "fetch_account", lambda token: dict(account or ACCOUNT_JSON)
    )
    monkeypatch.setattr(lichess_client, "revoke_token", lambda token: None)


def _sign_in(db, monkeypatch, account: dict | None = None) -> dict:
    _patch_oauth_success(monkeypatch, account)
    with _limiter_disabled():
        return login_lichess(
            request=_fake_request(),
            req=LichessLoginRequest(code=VALID_CODE, code_verifier=VALID_VERIFIER),
            db=db,
        )


# ---------------------------------------------------------------------------
# Client layer — httpx fakes (POST/GET/DELETE variants)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, body: object = None, headers: dict | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body


class _FakeClientCM:
    """Stand-in for ``httpx.Client(...)`` supporting get/post/delete."""

    def __init__(self, response: _FakeResponse, captured: dict | None = None):
        self._response = response
        self._captured = captured if captured is not None else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None):
        self._captured.update({"method": "GET", "url": url, "headers": headers})
        return self._response

    def post(self, url, headers=None, data=None):
        self._captured.update({"method": "POST", "url": url, "headers": headers, "data": data})
        return self._response

    def delete(self, url, headers=None):
        self._captured.update({"method": "DELETE", "url": url, "headers": headers})
        return self._response


def _patch_httpx(monkeypatch, response: _FakeResponse) -> dict:
    captured: dict = {}
    monkeypatch.setattr(
        lichess_client.httpx, "Client", lambda **kw: _FakeClientCM(response, captured)
    )
    return captured


class TestOAuthClient:
    def test_oa_01_exchange_posts_pkce_grant_and_returns_token(self, monkeypatch):
        captured = _patch_httpx(
            monkeypatch, _FakeResponse(200, {"access_token": "lio_abc123", "token_type": "Bearer"})
        )
        token = lichess_client.exchange_authorization_code(VALID_CODE, VALID_VERIFIER)
        assert token == "lio_abc123"
        assert captured["method"] == "POST"
        assert captured["url"].endswith("/api/token")
        assert captured["data"] == {
            "grant_type": "authorization_code",
            "code": VALID_CODE,
            "code_verifier": VALID_VERIFIER,
            "redirect_uri": lichess_client.LICHESS_OAUTH_REDIRECT_URI,
            "client_id": lichess_client.LICHESS_OAUTH_CLIENT_ID,
        }
        # The server-wide LICHESS_OAUTH_TOKEN must never ride along.
        assert "Authorization" not in (captured["headers"] or {})

    def test_oa_02_grant_rejection_raises_oauth_error(self, monkeypatch):
        _patch_httpx(monkeypatch, _FakeResponse(400, {"error": "invalid_grant"}))
        with pytest.raises(lichess_client.LichessOAuthError):
            lichess_client.exchange_authorization_code(VALID_CODE, VALID_VERIFIER)

    def test_oa_03_upstream_and_rate_limit_map_to_typed_errors(self, monkeypatch):
        _patch_httpx(monkeypatch, _FakeResponse(502))
        with pytest.raises(lichess_client.LichessUpstreamError):
            lichess_client.exchange_authorization_code(VALID_CODE, VALID_VERIFIER)
        _patch_httpx(monkeypatch, _FakeResponse(429, headers={"Retry-After": "13"}))
        with pytest.raises(lichess_client.LichessRateLimited) as excinfo:
            lichess_client.exchange_authorization_code(VALID_CODE, VALID_VERIFIER)
        assert excinfo.value.retry_after == 13

    def test_oa_04_malformed_inputs_rejected_before_network(self, monkeypatch):
        def _boom(**kw):  # pragma: no cover — must never run
            raise AssertionError("network reached with malformed input")

        monkeypatch.setattr(lichess_client.httpx, "Client", _boom)
        for code, verifier in [
            ("", VALID_VERIFIER),  # empty code
            ("has space", VALID_VERIFIER),  # non-printable-token code
            ("c\x00de", VALID_VERIFIER),  # control byte
            ("x" * 513, VALID_VERIFIER),  # oversize code
            (VALID_CODE, "short"),  # verifier under 43 chars
            (VALID_CODE, "a" * 129),  # verifier over 128 chars
            (VALID_CODE, "bad!chars" + "a" * 40),  # verifier outside unreserved set
        ]:
            with pytest.raises(lichess_client.LichessOAuthError):
                lichess_client.exchange_authorization_code(code, verifier)

    def test_oa_05_fetch_account_validates_id_fail_closed(self, monkeypatch):
        for hostile_id in ["../../admin", "a", "x" * 31, "", None, 42, "evil.com?"]:
            _patch_httpx(
                monkeypatch, _FakeResponse(200, {"id": hostile_id, "username": "whatever"})
            )
            with pytest.raises(lichess_client.LichessParseError):
                lichess_client.fetch_account("lio_sometoken")

    def test_oa_05b_fetch_account_happy_path_sends_user_token(self, monkeypatch):
        captured = _patch_httpx(monkeypatch, _FakeResponse(200, dict(ACCOUNT_JSON)))
        account = lichess_client.fetch_account("lio_sometoken")
        assert account["id"] == "chesswizard"
        assert captured["headers"]["Authorization"] == "Bearer lio_sometoken"

    def test_oa_06_fetch_account_401_raises_oauth_error(self, monkeypatch):
        _patch_httpx(monkeypatch, _FakeResponse(401))
        with pytest.raises(lichess_client.LichessOAuthError):
            lichess_client.fetch_account("lio_sometoken")

    def test_oa_07_revoke_token_never_raises(self, monkeypatch):
        import httpx as _httpx

        def _transport_error(**kw):
            raise _httpx.ConnectError("boom")

        monkeypatch.setattr(lichess_client.httpx, "Client", _transport_error)
        lichess_client.revoke_token("lio_sometoken")  # must not raise
        lichess_client.revoke_token("\x00malformed")  # must not raise


# ---------------------------------------------------------------------------
# Router + service layer
# ---------------------------------------------------------------------------


class TestLichessSignIn:
    def test_li_01_first_sign_in_creates_player(self, db_session, monkeypatch):
        result = _sign_in(db_session, monkeypatch)
        assert result["created"] is True
        assert result["token_type"] == "bearer"
        assert result["lichess_username"] == "ChessWizard"
        player = db_session.query(Player).filter_by(id=result["player_id"]).one()
        assert player.lichess_user_id == "chesswizard"
        assert player.email == "lichess:chesswizard"
        payload = decode_token(result["access_token"])
        assert payload["player_id"] == str(player.id)

    def test_li_02_second_sign_in_reuses_player(self, db_session, monkeypatch):
        first = _sign_in(db_session, monkeypatch)
        second = _sign_in(db_session, monkeypatch)
        assert second["created"] is False
        assert second["player_id"] == first["player_id"]
        assert db_session.query(Player).count() == 1

    def test_li_03_oauth_rejection_maps_to_401(self, db_session, monkeypatch):
        def _reject(code, verifier):
            raise lichess_client.LichessOAuthError("invalid_grant")

        monkeypatch.setattr(lichess_client, "exchange_authorization_code", _reject)
        with _limiter_disabled(), pytest.raises(HTTPException) as excinfo:
            login_lichess(
                request=_fake_request(),
                req=LichessLoginRequest(code=VALID_CODE, code_verifier=VALID_VERIFIER),
                db=db_session,
            )
        assert excinfo.value.status_code == 401
        assert db_session.query(Player).count() == 0

    @pytest.mark.parametrize(
        "exc, expected_status",
        [
            (lichess_client.LichessRateLimited("busy"), 503),
            (lichess_client.LichessUpstreamError("5xx"), 502),
            (lichess_client.LichessParseError("bad body"), 502),
        ],
    )
    def test_li_04_upstream_failures_map_to_documented_codes(
        self, db_session, monkeypatch, exc, expected_status
    ):
        def _fail(code, verifier):
            raise exc

        monkeypatch.setattr(lichess_client, "exchange_authorization_code", _fail)
        with _limiter_disabled(), pytest.raises(HTTPException) as excinfo:
            login_lichess(
                request=_fake_request(),
                req=LichessLoginRequest(code=VALID_CODE, code_verifier=VALID_VERIFIER),
                db=db_session,
            )
        assert excinfo.value.status_code == expected_status

    def test_li_05_schema_rejects_bad_verifier(self):
        with pytest.raises(ValidationError):
            LichessLoginRequest(code=VALID_CODE, code_verifier="too-short")
        with pytest.raises(ValidationError):
            LichessLoginRequest(code=VALID_CODE, code_verifier="spaces are illegal" + "a" * 30)

    def test_li_06_schema_rejects_control_chars_in_code(self):
        with pytest.raises(ValidationError):
            LichessLoginRequest(code="evil\r\ncode", code_verifier=VALID_VERIFIER)
        with pytest.raises(ValidationError):
            LichessLoginRequest(code="", code_verifier=VALID_VERIFIER)

    def test_li_07_auto_link_and_calibration_on_first_sign_in(self, db_session, monkeypatch):
        result = _sign_in(db_session, monkeypatch)
        link = (
            db_session.query(LinkedAccount)
            .filter_by(player_id=result["player_id"], platform=PLATFORM_LICHESS)
            .one()
        )
        assert link.external_username == "chesswizard"
        player = db_session.query(Player).filter_by(id=result["player_id"]).one()
        # First-link calibration from the rapid perf in ACCOUNT_JSON.
        assert player.rating == pytest.approx(1907.0)

    def test_li_08_cross_player_conflict_does_not_block_sign_in(self, db_session, monkeypatch):
        squatter = Player(
            email="squatter@test.com",
            password_hash="dummy",
            player_embedding="[]",
        )
        db_session.add(squatter)
        db_session.commit()
        db_session.add(
            LinkedAccount(
                player_id=squatter.id,
                platform=PLATFORM_LICHESS,
                external_username="chesswizard",
            )
        )
        db_session.commit()

        result = _sign_in(db_session, monkeypatch)
        assert result["created"] is True
        # The squatter keeps the link; the OAuth player gets none (v1
        # policy: never move links between accounts during sign-in).
        rows = (
            db_session.query(LinkedAccount)
            .filter_by(platform=PLATFORM_LICHESS, external_username="chesswizard")
            .all()
        )
        assert len(rows) == 1
        assert rows[0].player_id == squatter.id

    def test_li_09_existing_link_never_clobbered(self, db_session, monkeypatch):
        first = _sign_in(db_session, monkeypatch)
        link = (
            db_session.query(LinkedAccount)
            .filter_by(player_id=first["player_id"], platform=PLATFORM_LICHESS)
            .one()
        )
        # Simulate an import watermark, then sign in again: the row (and
        # its watermark) must survive untouched.
        from datetime import datetime

        watermark = datetime(2026, 1, 1, 12, 0, 0)
        link.last_imported_at = watermark
        db_session.commit()

        _sign_in(db_session, monkeypatch)
        refreshed = (
            db_session.query(LinkedAccount)
            .filter_by(player_id=first["player_id"], platform=PLATFORM_LICHESS)
            .one()
        )
        assert refreshed.last_imported_at == watermark

    def test_li_10_synthetic_email_namespace_cannot_be_squatted(self):
        with pytest.raises(ValidationError):
            RegisterRequest(email="lichess:chesswizard", password="hunter2hunter2")

    def test_li_11_password_login_never_matches_lichess_account(self, db_session, monkeypatch):
        _sign_in(db_session, monkeypatch)
        service = AuthService(db_session)
        # Login validators reject the synthetic email shape upstream, but
        # even a direct service call with guessed passwords must fail
        # against the unusable random credential.
        for guess in ["", "password", "lichess:chesswizard", "chesswizard"]:
            with pytest.raises(ValueError):
                service.login("lichess:chesswizard", guess)

    def test_li_12_issued_token_round_trips_session_validation(self, db_session, monkeypatch):
        result = _sign_in(db_session, monkeypatch)
        payload = decode_token(result["access_token"])
        service = AuthService(db_session)
        player = service.get_player_by_session(payload["session_id"], result["access_token"])
        assert player is not None
        assert str(player.id) == result["player_id"]
        # F-07: the session row pins sha256 of exactly this token.
        session = player.sessions[0]
        assert session.token_hash == hashlib.sha256(result["access_token"].encode()).hexdigest()

    def test_li_13_sign_in_survives_auto_link_failure(self, db_session, monkeypatch):
        _patch_oauth_success(monkeypatch)
        # The router imports import_service lazily (circular-import guard),
        # resolving link_account through the module object at call time —
        # so patching the module attribute is seen by the handler.
        import llm.seca.lichess.import_service as _import_service

        def _explode(db, player, username, *, profile=None):
            raise RuntimeError("link service down")

        monkeypatch.setattr(_import_service, "link_account", _explode)
        with _limiter_disabled():
            result = login_lichess(
                request=_fake_request(),
                req=LichessLoginRequest(code=VALID_CODE, code_verifier=VALID_VERIFIER),
                db=db_session,
            )
        assert result["created"] is True
        assert db_session.query(LinkedAccount).count() == 0
