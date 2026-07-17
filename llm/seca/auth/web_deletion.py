"""Public web account-deletion surface — GDPR Art. 17 + Google Play.

Google Play's User Data policy requires that an app which lets users
create accounts also expose a **web** resource, reachable WITHOUT the
app, where users can delete their account and its data (the in-app path
in ``AccountFlows.confirmAndDeleteAccount`` covers users who still have
the app; this covers those who uninstalled).  See
``docs/API_CONTRACTS.md`` §43.

Design: prove ownership, then delete on the spot.

* Password accounts — POST email + password + explicit confirm.
  ``AuthService.verify_credentials`` checks the credential timing-safely
  (no session minted), then ``erasure.purge_player_data`` erases
  everything.
* Lichess accounts — "Sign in with Lichess" runs a full authorization-
  code + PKCE flow whose redirect lands back HERE (an ``https://``
  callback, distinct from the app's custom-scheme one).  The proven
  Lichess identity is matched to ``players.lichess_user_id`` and erased.
  Lichess-only accounts have no usable password, so this is their only
  web path.

Security posture (this is an unauthenticated, irreversible surface):

* No account is ever deleted from a typed-in email alone — a valid
  password (password accounts) or a completed OAuth round-trip (Lichess)
  is mandatory, so this is not an anonymous account-deletion griefing
  vector.
* Tight per-IP rate limits (credential-checking + upstream OAuth).
* PKCE verifier + CSRF ``state`` travel in a signed, HttpOnly, Secure,
  SameSite=Lax cookie (HS256 over ``SECRET_KEY``); the callback rejects a
  missing / expired / state-mismatched cookie before touching Lichess.
* No user input is ever reflected into the served HTML (errors are fixed
  strings), so the pages carry no XSS surface.
* GET renders only; the destructive password path is POST-only.

Layering: reuses ``AuthService`` + ``erasure`` + the Lichess client; no
engine imports (auth-directory sweep in test_seca_layer_boundaries).
"""

from __future__ import annotations

# Slowapi reads ``request: Request`` off each rate-limited handler's
# signature even when the body doesn't reference it (the GET page +
# lichess/start handlers).  Disable file-wide rather than per-handler,
# matching llm/seca/auth/router.py.
# pylint: disable=unused-argument

import base64
import hashlib
import logging
import os
import secrets
from urllib.parse import parse_qs, urlencode

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession
from starlette.concurrency import run_in_threadpool

from llm.seca.auth.tokens import ALGORITHM, SECRET_KEY
from llm.seca.lichess import client as lichess_client
from llm.seca.shared_limiter import limiter

from .router import get_db
from .service import AuthService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["account-deletion"])

# Public origin used to build the Lichess OAuth callback.  Must be the
# externally-reachable https base (Caddy terminates TLS in prod); derived
# from an env var rather than request.base_url so a proxied http scheme
# can never leak into the redirect_uri.  Dev overrides to localhost.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://cereveon.com").rstrip("/")
_LICHESS_WEB_REDIRECT_URI = f"{PUBLIC_BASE_URL}/delete-account/lichess/callback"

# Signed PKCE/state cookie: short-lived, HttpOnly, scoped to the flow.
_PKCE_COOKIE = "cereveon_del_pkce"
_PKCE_TTL_SECONDS = 600
_COOKIE_PATH = "/delete-account"


# ---------------------------------------------------------------------------
# HTML rendering — self-contained (no external assets; inline CSS only, no
# JS).  Caddy sets no CSP, but inline-only keeps the surface trivially safe.
# ---------------------------------------------------------------------------

_STYLE = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; display: flex; align-items: center;
  justify-content: center; background: #12100e; color: #ece6da;
  font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.5; padding: 24px; }
.card { width: 100%; max-width: 460px; background: #1b1815;
  border: 1px solid #2e2a24; border-radius: 14px; padding: 32px; }
h1 { font-size: 1.5rem; margin: 0 0 4px; font-weight: 600; }
.brand { font-size: .8rem; letter-spacing: .18em; text-transform: uppercase;
  color: #8a7f6d; margin: 0 0 20px; }
p { color: #c9c0b0; font-size: .95rem; }
label { display: block; font-size: .85rem; color: #b8ad99; margin: 16px 0 6px; }
input[type=email], input[type=password] { width: 100%; padding: 12px 14px;
  background: #12100e; border: 1px solid #3a352d; border-radius: 8px;
  color: #ece6da; font-size: 1rem; }
.confirm { display: flex; gap: 10px; align-items: flex-start; margin: 20px 0 8px;
  font-size: .85rem; color: #b8ad99; }
.confirm input { margin-top: 3px; }
button { width: 100%; margin-top: 20px; padding: 13px; border: 0;
  border-radius: 8px; font-size: 1rem; font-weight: 600; cursor: pointer;
  background: #c2410c; color: #fff; }
.lichess { display: block; text-align: center; margin-top: 14px; padding: 13px;
  border: 1px solid #3a352d; border-radius: 8px; color: #ece6da;
  text-decoration: none; font-weight: 600; }
.note { font-size: .82rem; color: #8a7f6d; margin-top: 24px; }
.err { background: #3a1512; border: 1px solid #7f1d1d; color: #fca5a5;
  border-radius: 8px; padding: 12px 14px; font-size: .9rem; margin: 16px 0 0; }
.ok { color: #86efac; }
hr { border: 0; border-top: 1px solid #2e2a24; margin: 24px 0; }
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        "<meta name=robots content=noindex>"
        f"<title>{title}</title><style>{_STYLE}</style></head>"
        f"<body><main class=card>{body}</main></body></html>"
    )


def _form_page(error: str | None = None) -> str:
    err_html = f'<div class="err">{error}</div>' if error else ""
    # NOTE: `error` is only ever passed fixed literals from this module —
    # never user input — so it is safe to inline without escaping.
    return _page(
        "Delete your Cereveon account",
        f"""
        <p class="brand">Cereveon</p>
        <h1>Delete your account</h1>
        <p>This permanently deletes your account and everything in it —
        games, chat history, progress, study plans and imported Lichess
        games. This cannot be undone.</p>
        {err_html}
        <form method="post" action="/delete-account">
          <label for="email">Email</label>
          <input id="email" name="email" type="email" autocomplete="email" required>
          <label for="password">Password</label>
          <input id="password" name="password" type="password"
                 autocomplete="current-password" required>
          <label class="confirm">
            <input type="checkbox" name="confirm" value="yes" required>
            <span>I understand this permanently deletes my account and all of
            my data, and cannot be undone.</span>
          </label>
          <button type="submit">Delete my account</button>
        </form>
        <a class="lichess" href="/delete-account/lichess/start">
          Signed in with Lichess? Continue with Lichess</a>
        <hr>
        <p class="note">Still have the app installed? You can also delete your
        account instantly from Settings &rsaquo; Account &rsaquo; Delete
        account. Questions: privacy@cereveon.com</p>
        """,
    )


def _done_page() -> str:
    return _page(
        "Account deleted",
        """
        <p class="brand">Cereveon</p>
        <h1 class="ok">Your account was deleted</h1>
        <p>Your Cereveon account and all associated data have been permanently
        erased. There is nothing further you need to do.</p>
        <p class="note">Changed your mind later? You're welcome to create a new
        account in the app any time. Questions: privacy@cereveon.com</p>
        """,
    )


def _error_page(message: str) -> str:
    return _page(
        "Couldn't delete the account",
        f"""
        <p class="brand">Cereveon</p>
        <h1>Something went wrong</h1>
        <div class="err">{message}</div>
        <p style="margin-top:20px"><a class="lichess" href="/delete-account">
          Back to the deletion page</a></p>
        """,
    )


# ---------------------------------------------------------------------------
# PKCE / signed-cookie helpers
# ---------------------------------------------------------------------------


def _new_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` (RFC 7636 S256).

    ``token_urlsafe`` yields the unreserved ``[A-Za-z0-9_-]`` alphabet the
    verifier regex accepts; sliced to the 43..128 length window.
    """
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _sign_pkce_cookie(verifier: str, state: str) -> str:
    return jwt.encode({"v": verifier, "s": state}, SECRET_KEY, algorithm=ALGORITHM)


def _read_pkce_cookie(raw: str | None) -> tuple[str, str] | None:
    """Decode the signed cookie → ``(verifier, state)`` or ``None`` when
    absent / tampered / expired (PyJWT verifies the HS256 signature; the
    cookie's own ``max_age`` bounds its lifetime)."""
    if not raw:
        return None
    try:
        payload = jwt.decode(raw, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    verifier = payload.get("v")
    state = payload.get("s")
    if not isinstance(verifier, str) or not isinstance(state, str):
        return None
    return verifier, state


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/delete-account", response_class=HTMLResponse)
@limiter.limit("20/minute")
def delete_account_page(request: Request) -> HTMLResponse:
    """The public deletion page (GET renders only — never destructive)."""
    return HTMLResponse(_form_page())


@router.post("/delete-account", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def delete_account_submit(
    request: Request,
    db: DBSession = Depends(get_db),
) -> HTMLResponse:
    """Password-account deletion: verify credentials, then erase.

    The urlencoded body is parsed directly (``parse_qs``) rather than via
    ``request.form()`` — Starlette's form parser hard-requires the
    ``python-multipart`` package even for urlencoded bodies, and this
    form never uploads files, so the dependency is pure liability.
    Credential verification (PBKDF2, ~100 ms) and the purge run in a
    threadpool so the event loop is never blocked.
    """
    raw = (await request.body()).decode("utf-8", "replace")
    fields = parse_qs(raw, keep_blank_values=True)
    email = (fields.get("email", [""])[0]).strip()
    password = fields.get("password", [""])[0]
    confirmed = fields.get("confirm", [""])[0] == "yes"

    if not email or not password or not confirmed:
        return HTMLResponse(
            _form_page("Enter your email and password and tick the confirmation box."),
            status_code=400,
        )

    def _verify_and_purge() -> bool:
        # Lazy import: erasure pulls the coach model graph; keep it off
        # this module's import path (same rationale as delete_account).
        from .erasure import purge_player_data

        try:
            player = AuthService(db).verify_credentials(email, password)
        except ValueError:
            return False
        purge_player_data(db, str(player.id))
        return True

    ok = await run_in_threadpool(_verify_and_purge)
    if not ok:
        return HTMLResponse(
            _form_page("That email and password didn't match an account."),
            status_code=401,
        )
    logger.info("web account deletion: password account erased")
    return HTMLResponse(_done_page())


@router.get("/delete-account/lichess/start")
@limiter.limit("10/minute")
def delete_account_lichess_start(request: Request) -> RedirectResponse:
    """Begin the Lichess authorization-code + PKCE flow for deletion."""
    verifier, challenge = _new_pkce()
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": lichess_client.LICHESS_OAUTH_CLIENT_ID,
        "redirect_uri": _LICHESS_WEB_REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    }
    authorize_url = f"{lichess_client.LICHESS_API_BASE}/oauth?{urlencode(params)}"
    response = RedirectResponse(authorize_url, status_code=302)
    response.set_cookie(
        _PKCE_COOKIE,
        _sign_pkce_cookie(verifier, state),
        max_age=_PKCE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path=_COOKIE_PATH,
    )
    return response


@router.get("/delete-account/lichess/callback", response_class=HTMLResponse)
@limiter.limit("10/minute")
def delete_account_lichess_callback(
    request: Request,
    db: DBSession = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
) -> HTMLResponse:
    """Complete the Lichess flow: verify state, exchange, match, erase."""
    from llm.seca.auth.models import Player

    def _clear(resp: HTMLResponse) -> HTMLResponse:
        resp.delete_cookie(_PKCE_COOKIE, path=_COOKIE_PATH)
        return resp

    cookie = _read_pkce_cookie(request.cookies.get(_PKCE_COOKIE))
    if cookie is None or not code or not state or state != cookie[1]:
        # Missing/expired/tampered cookie or CSRF state mismatch.
        return _clear(
            HTMLResponse(
                _error_page("Your sign-in link expired. Please start again."),
                status_code=400,
            )
        )
    verifier = cookie[0]

    try:
        token = lichess_client.exchange_authorization_code(
            code, verifier, redirect_uri=_LICHESS_WEB_REDIRECT_URI
        )
        account = lichess_client.fetch_account(token)
    except lichess_client.LichessClientError:
        return _clear(
            HTMLResponse(
                _error_page("We couldn't confirm your Lichess sign-in. Please try again."),
                status_code=502,
            )
        )

    lichess_id = account.get("id")
    player = None
    if isinstance(lichess_id, str):
        player = db.query(Player).filter(Player.lichess_user_id == lichess_id).first()

    if player is None:
        # Proven Lichess identity, but no Cereveon account is linked to it.
        lichess_client.revoke_token(token)
        return _clear(
            HTMLResponse(
                _error_page("No Cereveon account is linked to that Lichess login."),
                status_code=404,
            )
        )

    from .erasure import purge_player_data

    purge_player_data(db, str(player.id))
    lichess_client.revoke_token(token)
    logger.info("web account deletion: lichess account erased")
    return _clear(HTMLResponse(_done_page()))
