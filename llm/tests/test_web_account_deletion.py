"""
Backend tests for the public web account-deletion page — GDPR Art. 17 +
Google Play (GET/POST /delete-account*, docs/API_CONTRACTS.md §43).

Exercised through a real FastAPI app + TestClient so the urlencoded form
parse, the signed PKCE cookie, and the OAuth redirect/callback all run
end-to-end.  The Lichess upstream is monkeypatched at the client seam.

Pinned invariants
-----------------
 1. WD_PAGE_PUBLIC          GET /delete-account → 200 HTML, no auth, form + Lichess link.
 2. WD_GET_NOT_DESTRUCTIVE  GET never deletes (rendering the page twice leaves rows intact).
 3. WD_PASSWORD_DELETES     POST correct email+password+confirm → 200, player + child rows gone.
 4. WD_WRONG_PASSWORD       POST wrong password → 401, account intact.
 5. WD_UNKNOWN_EMAIL        POST unknown email → 401, nothing deleted.
 6. WD_CONFIRM_REQUIRED     POST without the confirm box → 400, account intact.
 7. WD_LICHESS_START        GET /lichess/start → 302 to lichess.org/oauth with
                            S256 challenge + state + web redirect_uri; sets signed cookie.
 8. WD_LICHESS_DELETES      full start→callback with a matching lichess_user_id erases it.
 9. WD_LICHESS_CSRF         callback with no cookie / mismatched state → 400, intact.
10. WD_LICHESS_NO_MATCH     proven Lichess identity with no linked account → 404, no delete.
11. WD_EXCHANGE_REDIRECT    exchange_authorization_code forwards a redirect_uri override.
12. WD_VERIFY_NO_SESSION    AuthService.verify_credentials issues no session.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

# Importing erasure registers every model on Base before create_all.
from llm.seca.auth import erasure  # noqa: F401
from llm.seca.auth.hashing import hash_password
from llm.seca.auth.models import Base, Player, Session
from llm.seca.auth.router import get_db
from llm.seca.auth.service import AuthService
from llm.seca.auth.web_deletion import _PKCE_COOKIE, router as web_deletion_router
from llm.seca.chat.models import ChatTurn
from llm.seca.lichess import client as lichess_client
from llm.seca.shared_limiter import limiter

_PASSWORD = "correct-horse-battery"


@pytest.fixture()
def sessionmaker_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared in-memory DB across TestClient threads
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def client(sessionmaker_fixture):
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(web_deletion_router)

    def _override_get_db():
        db = sessionmaker_fixture()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db

    prev = limiter.enabled
    limiter.enabled = False
    try:
        # https base so the Secure PKCE cookie is sent back on the callback.
        yield TestClient(app, base_url="https://testserver")
    finally:
        limiter.enabled = prev


def _seed_password_player(SessionLocal, tag: str = "pw") -> str:
    db = SessionLocal()
    try:
        pid = f"player-{tag}"
        db.add(Player(id=pid, email=f"{tag}@del.test", password_hash=hash_password(_PASSWORD)))
        db.add(ChatTurn(player_id=pid, role="user", content="hello"))
        db.commit()
        return pid
    finally:
        db.close()


def _seed_lichess_player(SessionLocal, lichess_id: str = "lichuser") -> str:
    db = SessionLocal()
    try:
        pid = f"player-{lichess_id}"
        db.add(
            Player(
                id=pid,
                email=f"lichess:{lichess_id}",
                password_hash="!unusable",
                lichess_user_id=lichess_id,
            )
        )
        db.add(ChatTurn(player_id=pid, role="user", content="hi"))
        db.commit()
        return pid
    finally:
        db.close()


def _exists(SessionLocal, pid: str) -> bool:
    db = SessionLocal()
    try:
        return db.query(Player).filter_by(id=pid).first() is not None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Page + password path
# ---------------------------------------------------------------------------


def test_page_is_public_and_renders_form(client):
    """WD_PAGE_PUBLIC."""
    r = client.get("/delete-account")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Delete my account" in body
    assert 'action="/delete-account"' in body
    assert "/delete-account/lichess/start" in body


def test_get_never_deletes(client, sessionmaker_fixture):
    """WD_GET_NOT_DESTRUCTIVE."""
    pid = _seed_password_player(sessionmaker_fixture)
    client.get("/delete-account")
    client.get("/delete-account")
    assert _exists(sessionmaker_fixture, pid)


def test_password_deletes_account_and_children(client, sessionmaker_fixture):
    """WD_PASSWORD_DELETES."""
    pid = _seed_password_player(sessionmaker_fixture)
    r = client.post(
        "/delete-account",
        data={"email": "pw@del.test", "password": _PASSWORD, "confirm": "yes"},
    )
    assert r.status_code == 200
    assert "deleted" in r.text.lower()
    assert not _exists(sessionmaker_fixture, pid)
    db = sessionmaker_fixture()
    try:
        assert db.query(ChatTurn).filter_by(player_id=pid).count() == 0
    finally:
        db.close()


def test_wrong_password_keeps_account(client, sessionmaker_fixture):
    """WD_WRONG_PASSWORD."""
    pid = _seed_password_player(sessionmaker_fixture)
    r = client.post(
        "/delete-account",
        data={"email": "pw@del.test", "password": "not-the-password", "confirm": "yes"},
    )
    assert r.status_code == 401
    assert _exists(sessionmaker_fixture, pid)


def test_unknown_email_deletes_nothing(client, sessionmaker_fixture):
    """WD_UNKNOWN_EMAIL."""
    pid = _seed_password_player(sessionmaker_fixture)
    r = client.post(
        "/delete-account",
        data={"email": "nobody@del.test", "password": _PASSWORD, "confirm": "yes"},
    )
    assert r.status_code == 401
    assert _exists(sessionmaker_fixture, pid)


def test_confirm_checkbox_required(client, sessionmaker_fixture):
    """WD_CONFIRM_REQUIRED."""
    pid = _seed_password_player(sessionmaker_fixture)
    r = client.post(
        "/delete-account",
        data={"email": "pw@del.test", "password": _PASSWORD},  # no confirm
    )
    assert r.status_code == 400
    assert _exists(sessionmaker_fixture, pid)


# ---------------------------------------------------------------------------
# Lichess path
# ---------------------------------------------------------------------------


def test_lichess_start_redirects_and_sets_cookie(client):
    """WD_LICHESS_START."""
    r = client.get("/delete-account/lichess/start", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    parsed = urlparse(loc)
    assert parsed.netloc.endswith("lichess.org")
    assert parsed.path == "/oauth"
    q = parse_qs(parsed.query)
    assert q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] and q["state"]
    assert q["redirect_uri"][0].endswith("/delete-account/lichess/callback")
    assert _PKCE_COOKIE in r.cookies


def test_lichess_callback_deletes_matching_account(client, sessionmaker_fixture, monkeypatch):
    """WD_LICHESS_DELETES."""
    pid = _seed_lichess_player(sessionmaker_fixture, "lichuser")
    monkeypatch.setattr(lichess_client, "exchange_authorization_code", lambda *a, **k: "tok-abc")
    monkeypatch.setattr(lichess_client, "fetch_account", lambda *a, **k: {"id": "lichuser"})
    revoked = {}
    monkeypatch.setattr(lichess_client, "revoke_token", lambda t: revoked.update(t=t))

    start = client.get("/delete-account/lichess/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    r = client.get(f"/delete-account/lichess/callback?code=validcode123&state={state}")
    assert r.status_code == 200
    assert "deleted" in r.text.lower()
    assert not _exists(sessionmaker_fixture, pid)
    assert revoked.get("t") == "tok-abc"  # token revoked after use


def test_lichess_callback_rejects_csrf(client, sessionmaker_fixture, monkeypatch):
    """WD_LICHESS_CSRF: mismatched state (or no cookie) never deletes."""
    pid = _seed_lichess_player(sessionmaker_fixture, "lichuser")
    called = {"exchange": False}
    monkeypatch.setattr(
        lichess_client,
        "exchange_authorization_code",
        lambda *a, **k: called.update(exchange=True) or "tok",
    )

    client.get("/delete-account/lichess/start", follow_redirects=False)
    # Wrong state — the cookie's state won't match.
    r = client.get("/delete-account/lichess/callback?code=validcode123&state=forged")
    assert r.status_code == 400
    assert _exists(sessionmaker_fixture, pid)
    assert called["exchange"] is False  # never reached the upstream


def test_lichess_callback_no_linked_account(client, sessionmaker_fixture, monkeypatch):
    """WD_LICHESS_NO_MATCH."""
    monkeypatch.setattr(lichess_client, "exchange_authorization_code", lambda *a, **k: "tok")
    monkeypatch.setattr(lichess_client, "fetch_account", lambda *a, **k: {"id": "stranger"})
    monkeypatch.setattr(lichess_client, "revoke_token", lambda t: None)

    start = client.get("/delete-account/lichess/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    r = client.get(f"/delete-account/lichess/callback?code=validcode123&state={state}")
    assert r.status_code == 404
    assert "no cereveon account" in r.text.lower()


# ---------------------------------------------------------------------------
# Reused-primitive pins
# ---------------------------------------------------------------------------


def test_exchange_forwards_redirect_uri_override(monkeypatch):
    """WD_EXCHANGE_REDIRECT: the additive redirect_uri param reaches the wire."""
    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "a" * 40}

    def _fake_request(method, url, **kwargs):
        captured.update(kwargs.get("data") or {})
        return _Resp()

    monkeypatch.setattr(lichess_client, "_request_json_bounded", _fake_request)
    verifier = "v" * 50  # matches CODE_VERIFIER_RE
    lichess_client.exchange_authorization_code(
        "codeXYZ", verifier, redirect_uri="https://cereveon.com/delete-account/lichess/callback"
    )
    assert captured["redirect_uri"] == "https://cereveon.com/delete-account/lichess/callback"


def test_verify_credentials_issues_no_session(sessionmaker_fixture):
    """WD_VERIFY_NO_SESSION."""
    pid = _seed_password_player(sessionmaker_fixture, "vc")
    db = sessionmaker_fixture()
    try:
        player = AuthService(db).verify_credentials("vc@del.test", _PASSWORD)
        assert player.id == pid
        assert db.query(Session).filter_by(player_id=pid).count() == 0
        with pytest.raises(ValueError):
            AuthService(db).verify_credentials("vc@del.test", "wrong")
        with pytest.raises(ValueError):
            AuthService(db).verify_credentials("ghost@del.test", _PASSWORD)
    finally:
        db.close()
