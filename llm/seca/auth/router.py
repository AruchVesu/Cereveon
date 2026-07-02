# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it.  Pylint
# flags every such parameter as unused; disabling the rule file-wide
# rather than per-handler keeps the diff stable as new endpoints land.
# pylint: disable=unused-argument

import json
import logging
import os

import jwt
from fastapi import APIRouter, Depends, HTTPException, Header, Request, Response
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session as DBSession
from llm.seca.shared_limiter import limiter

from .models import Base

# --- ensure ALL models are registered ---
# pylint: disable=wildcard-import,unused-wildcard-import
# These wildcard imports are LOAD-BEARING, not lazy: every model class
# under llm.seca.* must be imported before ``Base.metadata.create_all`` runs
# at lifespan startup so SQLAlchemy knows about every table.  Refactoring
# to explicit imports would force a manual maintenance burden every time
# a model is added.  Pylint correctly flags the pattern; we silence it
# for this single import block.
from llm.seca.auth.models import *  # noqa: F401,F403
from llm.seca.events.models import *  # noqa: F401,F403
from llm.seca.brain.models import *  # noqa: F401,F403
from llm.seca.analytics.models import *  # noqa: F401,F403
from llm.seca.storage.models import *  # noqa: F401,F403
from llm.seca.chat.models import *  # noqa: F401,F403
from llm.seca.lichess.models import *  # noqa: F401,F403
from llm.seca.training.models import *  # noqa: F401,F403
from llm.seca.coach.study_plan.models import *  # noqa: F401,F403

# pylint: enable=wildcard-import,unused-wildcard-import

# Lichess OAuth sign-in (POST /auth/lichess).  Only the HTTP client may be
# imported at module level: it imports nothing from the auth package.  The
# link service (llm.seca.lichess.import_service) imports ``engine`` FROM
# THIS MODULE, so importing it up here would be a circular import that
# breaks whenever auth.router loads first — it is imported lazily inside
# ``_ensure_lichess_link`` instead.
from llm.seca.lichess import client as lichess_client

from .service import AuthService
from .tokens import create_access_token, decode_token

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/seca.db")
_is_sqlite = DATABASE_URL.startswith("sqlite")

# create_engine itself does not open a connection — engine creation is
# metadata-only.  Actual DDL / I/O happens later in init_schema(), which is
# called from FastAPI lifespan and (for tests that bypass lifespan) from
# the session-scoped autouse fixture in llm/conftest.py.
if _is_sqlite:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(bind=engine)


def _column_type_for_dialect(sqlite_type: str, pg_type: str) -> str:
    """Pick the SQL type literal appropriate for the current engine."""
    return sqlite_type if _is_sqlite else pg_type


def _ensure_column(conn, table: str, column: str, sql_type: str) -> None:
    """Idempotent ``ALTER TABLE ... ADD COLUMN`` for SQLite + Postgres.

    Uses SQLAlchemy's ``inspect`` so we don't have to dialect-switch on the
    PRAGMA / information_schema query.  Both dialects accept the
    ``ALTER TABLE <t> ADD COLUMN <c> <type>`` form (no DEFAULT, NULL
    allowed) — that's the lowest-common-denominator portable DDL.
    """
    cols = {c["name"] for c in inspect(engine).get_columns(table)}
    if column in cols:
        return
    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}'))
    conn.commit()


def init_schema() -> None:
    """Create the SQLAlchemy schema and apply small in-place migrations.

    Idempotent — safe to call from FastAPI lifespan, the test conftest
    fixture, or maintenance scripts.  Performs:

    1.  For SQLite, ensure ``data/`` exists so the file-based DB can be
        created on first connect.
    2.  ``Base.metadata.create_all`` — adds any missing tables (creates
        new tables on Postgres + SQLite; does NOT alter existing tables).
    3.  Best-effort ``ADD COLUMN`` for columns that exist in the SQLAlchemy
        models but not on the live table.  Required because
        ``create_all`` is no-op for tables that already exist, even when
        a column has been added to the model since the table was last
        created.  Without this step, an authenticated request that
        relies on the new column 500s (the production symptom that
        prompted this helper: PR #135 added
        ``sessions.previous_token_hash`` + ``previous_token_expires_at``
        to the model; Postgres never got the columns; every login
        500'd until this migration shipped).

    Must NOT run at module-import time: import-time DDL slows ``import
    llm.seca.auth.router`` (used by every backend test for its Pydantic
    schemas) and couples module loading to filesystem / DB I/O.
    """
    if _is_sqlite:
        os.makedirs("data", exist_ok=True)

    Base.metadata.create_all(bind=engine)

    # In-place column migrations.  Both SQLite (legacy files) and
    # Postgres (live production) need these — Postgres because
    # ``create_all`` doesn't alter existing tables, SQLite because we
    # have user files that pre-date the column additions.
    with engine.connect() as conn:
        # Player.player_embedding (added pre-F-07 era).
        _ensure_column(
            conn,
            "players",
            "player_embedding",
            _column_type_for_dialect("TEXT DEFAULT '[]'", "TEXT DEFAULT '[]'"),
        )

        # Player.training_xp — monotonic per-player XP counter that
        # replaces the user-visible Elo kicker on the Android Home
        # screen.  DEFAULT 0 backfills legacy rows so /auth/me never
        # returns NULL for this field (Mapped[int] is non-Optional).
        _ensure_column(
            conn,
            "players",
            "training_xp",
            "INTEGER DEFAULT 0",
        )

        # Player.lichess_user_id — OAuth identity for "Sign in with
        # Lichess" (POST /auth/lichess).  NULL for password accounts.
        # The UNIQUE guarantee is added as a separate index because
        # SQLite's ALTER TABLE ADD COLUMN cannot carry UNIQUE; the index
        # name matches what the model's ``unique=True, index=True``
        # produces on fresh ``create_all`` tables, so this statement is a
        # no-op there.  Multiple NULLs are permitted by unique indexes on
        # both dialects, so legacy password-only rows are unaffected.
        _ensure_column(
            conn,
            "players",
            "lichess_user_id",
            _column_type_for_dialect("TEXT", "VARCHAR"),
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_players_lichess_user_id "
                "ON players (lichess_user_id)"
            )
        )
        conn.commit()

        # F-07 rotation race grace window — see Session model + service.
        # Sessions table pre-dates these columns on both dialects.
        # Both columns nullable so existing rows pass through without a
        # backfill.  SQLite uses TEXT/DATETIME, Postgres uses
        # VARCHAR/TIMESTAMP — both ANSI-compatible enough that the
        # lowest-common-denominator names ("TEXT", "TIMESTAMP") work
        # on both, but emitting the dialect-native ones keeps generated
        # tables matching what ``create_all`` would have produced.
        _ensure_column(
            conn,
            "sessions",
            "previous_token_hash",
            _column_type_for_dialect("TEXT", "VARCHAR"),
        )
        _ensure_column(
            conn,
            "sessions",
            "previous_token_expires_at",
            _column_type_for_dialect("DATETIME", "TIMESTAMP"),
        )

        # Lichess import (PR: Lichess API integration).  Adds
        # provenance + external-id columns to ``game_events`` so
        # imported Lichess games coexist with in-app games in the
        # same table without breaking the existing /game/finish
        # writer (NULL for legacy rows; populated by the Lichess
        # import service).  Both columns nullable so historical rows
        # need no backfill.  Lowest-common-denominator types: TEXT
        # on SQLite, VARCHAR on Postgres.
        _ensure_column(
            conn,
            "game_events",
            "source",
            _column_type_for_dialect("TEXT", "VARCHAR"),
        )
        _ensure_column(
            conn,
            "game_events",
            "external_game_id",
            _column_type_for_dialect("TEXT", "VARCHAR"),
        )

        # Per-game chat in game history.  ``app_game_id`` links a finished
        # game_events row back to the live ``games.id`` (== chat_turns.game_id)
        # so the history UI can load each game's coaching thread.  Nullable:
        # legacy rows, Lichess imports, and pre-game_id clients have none.  No
        # index — projection-only (the chat lookup hits chat_turns.game_id).
        _ensure_column(
            conn,
            "game_events",
            "app_game_id",
            _column_type_for_dialect("TEXT", "VARCHAR"),
        )

        # Per-game chat scoping ("chat history for each game"). game_id is
        # nullable so legacy rows stay player-global and player_id remains the
        # isolation boundary. Add the covering index for the per-game query;
        # CREATE INDEX IF NOT EXISTS is portable across SQLite + Postgres.
        _ensure_column(
            conn,
            "chat_turns",
            "game_id",
            _column_type_for_dialect("TEXT", "VARCHAR"),
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_chat_turn_player_game_created "
                "ON chat_turns (player_id, game_id, created_at)"
            )
        )
        conn.commit()

        # Weekly-curriculum anchor (aggregate-weakness re-anchor).  Records
        # the player's dominant MistakeCategory the study plan is built
        # around so the day-3 / day-7 practice puzzles can be selected from
        # that category's theme set.  Nullable: legacy plan rows and plans
        # created before a dominant category could be derived have none, and
        # fall back to the day-0 mistake's own theme.  Postgres needs the
        # ADD COLUMN because create_all skips existing tables; SQLite for
        # dev files that pre-date the column.
        _ensure_column(
            conn,
            "mistake_study_plans",
            "anchor_category",
            _column_type_for_dialect("TEXT", "VARCHAR"),
        )

        # Lichess background-import jobs (PR: v2 async import).  One
        # non-terminal row per player is enforced by:
        #   (a) llm.seca.lichess.get_player_import_lock — primary
        #       guard, works on both dialects.
        #   (b) THIS partial unique index — defense-in-depth on
        #       Postgres against a future caller that bypasses the
        #       service-layer lock.
        # SQLite supports partial indexes since 3.8.0 but the
        # ``WHERE status IN (...)`` form is finicky to round-trip
        # via SQLAlchemy reflection, and the lock covers dev anyway,
        # so we gate this DDL on Postgres only.
        if not _is_sqlite:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ix_lichess_import_jobs_one_active_per_player "
                    "ON lichess_import_jobs (player_id) "
                    "WHERE status IN ('queued', 'running')"
                )
            )
            conn.commit()


router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------
# Dependency
# ---------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_player(
    request: Request,
    response: Response,
    authorization: str | None = Header(None),
    db: DBSession = Depends(get_db),
):
    """Validate the Bearer token and return the matched Player.

    Side effect: on successful validation, mints a fresh JWT and stashes
    ``(session_id, new_token)`` on ``request.state.pending_auth_rotation``.
    The actual rotation (DB write + ``X-Auth-Token`` response header) is
    committed by the ``commit_pending_auth_rotation`` ASGI middleware
    registered on the FastAPI app — but ONLY when the response status is
    2xx.  Splitting the work this way fixes the cascading-401 lockout
    documented in issue #130: if the route handler then 5xxs (e.g. an
    LLM transient surfaces past every retry layer), the rotation never
    commits and the same JWT remains valid for the user's next request.

    Combined with the sliding session window in
    [AuthService.get_player_by_session], this gives the Android client a
    transparent refresh path so the JWT exp can stay tight (24h) without
    bouncing active users:
       - Active user: every authenticated call hands back a fresh
         24h JWT, the client saves it, the session slides forward.
       - Idle user: the JWT eventually expires; next call returns
         401 and the client routes to login.

    The new token is NOT issued on the failure paths (this dep raises
    HTTPException before reaching the mint step) and NOT committed on
    5xx routes (the middleware checks ``response.status_code``) — an
    attacker probing with a stolen-then-revoked token must not receive
    a fresh JWT they could keep using.

    Header semantics
    ----------------
    ``authorization`` is declared ``str | None = Header(None)`` rather
    than ``Header(...)`` so a request without an ``Authorization``
    header surfaces as a clean 401 from this dependency.  With the
    ellipsis form, FastAPI/Pydantic raises a 422 ValidationError before
    the function body runs, which the Android client can't translate
    into a "log in again" UX (it only special-cases 401).  See
    AUTH_HDR_01 / AUTH_HDR_02 in test_auth_missing_header.py.

    Compatibility shim
    ------------------
    ``response`` is kept in the signature for test callers that invoke
    this dependency directly (e.g. ``_call_get_current_player`` in
    ``test_auth_refresh_header.py``).  It is no longer written to here
    — the middleware owns the response header.  Tests asserting on the
    rotation outcome should use TestClient against a real authenticated
    route to exercise the full handler→middleware path.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        payload = decode_token(token)
    except jwt.InvalidTokenError:
        # Narrow catch: only JWT-level failures (expired / tampered /
        # wrong secret / malformed / alg:none) map to 401.  Any other
        # exception out of decode_token — pyjwt API drift, import-time
        # failure, SECRET_KEY length guard misfire — is a programming /
        # deploy error that must surface as a 500 with a traceback so
        # operators see it, not be silently downgraded to a 401 storm
        # in which "users can't log in" is the only symptom.
        raise HTTPException(status_code=401, detail="Invalid token")

    service = AuthService(db)
    player = service.get_player_by_session(payload["session_id"], token)

    if not player:
        raise HTTPException(status_code=401, detail="Session invalid")

    # Mint a fresh JWT for this session.  The middleware commits the
    # rotation (DB write + response header) only if the eventual
    # response is 2xx — see issue #130.
    new_token = create_access_token(
        player_id=str(player.id),
        session_id=payload["session_id"],
    )
    request.state.pending_auth_rotation = {
        "session_id": payload["session_id"],
        "new_token": new_token,
    }
    # The ``response`` parameter is intentionally untouched here so the
    # rotation header is sourced exclusively from the middleware path.
    _ = response
    return player


# ---------------------------
# Schemas
# ---------------------------
import re

from pydantic import BaseModel, field_validator

# RFC-shape email check.  Rejects:
#   - control characters and whitespace anywhere in the address
#   - missing local part, missing domain, missing TLD
#   - multiple '@', angle brackets, and other XSS / log-injection shapes
# This is intentionally stricter than the previous "contains '@'" check.
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Bytes 0x00–0x1f (C0 controls) and 0x7f (DEL).  These have no legitimate
# place in any user-supplied identifier or device string and enable log
# injection / XSS shapes if a downstream consumer ever displays the value.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _validate_email_strict(v: str) -> str:
    v = v.strip()
    if len(v) < 3 or len(v) > 320:
        raise ValueError("Invalid email address")
    if not _EMAIL_RE.fullmatch(v):
        raise ValueError("Invalid email address")
    return v


def _reject_control_chars(field: str, v: str) -> str:
    if _CONTROL_CHARS_RE.search(v):
        raise ValueError(f"{field} contains control characters")
    return v


class RegisterRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return _validate_email_strict(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) > 1000:
            raise ValueError("password too long (max 1000 chars)")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str
    device_info: str = ""

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return _validate_email_strict(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) > 1000:
            raise ValueError("password too long (max 1000 chars)")
        return v

    @field_validator("device_info")
    @classmethod
    def validate_device_info(cls, v: str) -> str:
        if len(v) > 200:
            raise ValueError("device_info too long (max 200 chars)")
        return _reject_control_chars("device_info", v)


class LichessLoginRequest(BaseModel):
    """POST /auth/lichess — OAuth authorization-code sign-in.

    The Android app runs the Lichess PKCE authorization flow in the
    system browser and forwards the resulting one-time ``code`` plus its
    ``code_verifier`` here.  The SERVER performs the code exchange
    (``llm.seca.lichess.client.exchange_authorization_code``) so Lichess
    access tokens never live on the device and tokens minted for other
    apps cannot be replayed into a Cereveon sign-in.
    """

    code: str
    code_verifier: str
    device_info: str = ""

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        v = v.strip()
        if not lichess_client.AUTH_CODE_RE.fullmatch(v):
            raise ValueError("malformed authorization code")
        return v

    @field_validator("code_verifier")
    @classmethod
    def validate_code_verifier(cls, v: str) -> str:
        if not lichess_client.CODE_VERIFIER_RE.fullmatch(v):
            raise ValueError("malformed code_verifier (RFC 7636 §4.1 shape required)")
        return v

    @field_validator("device_info")
    @classmethod
    def validate_device_info(cls, v: str) -> str:
        if len(v) > 200:
            raise ValueError("device_info too long (max 200 chars)")
        return _reject_control_chars("device_info", v)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("current_password", "new_password")
    @classmethod
    def validate_password_length(cls, v: str) -> str:
        if len(v) > 1000:
            raise ValueError("password too long (max 1000 chars)")
        return v


class UpdateMeRequest(BaseModel):
    """Partial update to the authenticated player's profile.

    Both fields are optional so the client can update them independently.
    The Android Onboarding screen sends rating + confidence together, but
    a future "I want to bump my rating" affordance could send just one.

    Bounds rationale
    ----------------
    * rating: ``(0, 4000]`` — the slider's effective range is 800–2600
      and Stockfish tops out around 3700, so 4000 is a comfortable
      defensive ceiling.  We reject 0 and negatives so a corrupt slider
      value never erases a real rating server-side.
    * confidence: ``[0.0, 1.0]`` — the adaptation layer treats this as
      an uncertainty band, so anything outside [0,1] would either crash
      downstream or silently be misinterpreted.
    """

    rating: float | None = None
    confidence: float | None = None

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if not 0.0 < v <= 4000.0:
            raise ValueError("rating must be in (0, 4000]")
        return float(v)

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be in [0.0, 1.0]")
        return float(v)


from llm.observability import auth_login_total, auth_register_total


# ---------------------------
# Endpoints
# ---------------------------
@router.post("/register")
@limiter.limit("5/minute")
def register(request: Request, req: RegisterRequest, db: DBSession = Depends(get_db)):
    service = AuthService(db)
    try:
        player = service.register(req.email, req.password)
    except ValueError:
        auth_register_total.labels(result="duplicate_or_invalid").inc()
        raise HTTPException(status_code=400, detail="Registration failed")
    token, _ = service.login(req.email, req.password, device_info="register")
    auth_register_total.labels(result="success").inc()
    return {
        "access_token": token,
        "player_id": str(player.id),
        "token_type": "bearer",
    }


@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, req: LoginRequest, db: DBSession = Depends(get_db)):
    service = AuthService(db)
    try:
        token, player = service.login(req.email, req.password, req.device_info)
    except ValueError:
        auth_login_total.labels(result="invalid_credentials").inc()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    auth_login_total.labels(result="success").inc()
    return {
        "access_token": token,
        "player_id": str(player.id),
        "token_type": "bearer",
    }


def _ensure_lichess_link(db: DBSession, player, account: dict) -> None:
    """Best-effort auto-link for OAuth sign-ins.

    An OAuth-verified identity is strictly stronger proof of account
    ownership than the self-asserted username in POST /lichess/link, so
    the first sign-in creates the game-import link (plus first-link
    rating calibration) automatically.  Skipped when the player already
    has ANY lichess link — including one pointing at a different handle —
    so this path never clobbers import watermarks or moves links between
    accounts.

    Never raises: a link or calibration failure must not fail a sign-in
    whose identity is already verified.  The rollback keeps a failed
    INSERT from poisoning the connection for the caller (Postgres
    InFailedSqlTransaction cascades otherwise).
    """
    # Local import: import_service imports ``engine`` from THIS module, so
    # a module-level import here would be circular (see the module-level
    # lichess_client import note).  By request time this module is fully
    # initialised and the import is a sys.modules lookup.
    from llm.seca.lichess import import_service as lichess_import_service
    from llm.seca.lichess.models import LinkedAccount

    # Captured before the try: reading player.id on the failure path could
    # itself raise (expired ORM instance on a dead connection), turning the
    # warning line into a post-commit 500.
    player_id = player.id
    try:
        existing = (
            db.query(LinkedAccount)
            .filter(
                LinkedAccount.player_id == player_id,
                LinkedAccount.platform == lichess_import_service.PLATFORM_LICHESS,
            )
            .first()
        )
        if existing is not None:
            return
        lichess_import_service.link_account(db, player, str(account["id"]), profile=account)
    except Exception:  # pylint: disable=broad-exception-caught
        db.rollback()
        logger.warning("lichess auto-link failed for player %s", player_id, exc_info=True)


@router.post("/lichess")
@limiter.limit("10/minute")
def login_lichess(request: Request, req: LichessLoginRequest, db: DBSession = Depends(get_db)):
    """Sign in — or transparently sign up — with a Lichess account.

    Flow (all Lichess I/O is server-side; see LichessLoginRequest):

    1. Exchange ``code`` + ``code_verifier`` at Lichess (PKCE, public
       client, pinned client_id / redirect_uri).
    2. ``GET /api/account`` with the resulting token → verified canonical
       Lichess user id (shape-validated fail-closed by the client).
    3. Revoke the Lichess token (best-effort) — identity proven, the
       credential is not needed again.
    4. Find-or-create the player keyed on ``players.lichess_user_id`` and
       issue a session JWT via the same machinery as /auth/login.
    5. Best-effort game-import auto-link + first-link calibration.
    """
    try:
        lichess_token = lichess_client.exchange_authorization_code(req.code, req.code_verifier)
        try:
            account = lichess_client.fetch_account(lichess_token)
        except lichess_client.LichessClientError:
            # The exchange succeeded, so a live token exists at Lichess even
            # though this sign-in is about to fail — don't leave it dangling
            # until natural expiry.  Best-effort; never raises.
            lichess_client.revoke_token(lichess_token)
            raise
    except lichess_client.LichessOAuthError:
        auth_login_total.labels(result="lichess_oauth_failed").inc()
        raise HTTPException(status_code=401, detail="Lichess sign-in failed")
    except lichess_client.LichessRateLimited:
        auth_login_total.labels(result="lichess_rate_limited").inc()
        raise HTTPException(status_code=503, detail="Lichess is busy; try again shortly")
    except lichess_client.LichessClientError:
        auth_login_total.labels(result="lichess_upstream_error").inc()
        raise HTTPException(status_code=502, detail="Lichess upstream error")

    lichess_client.revoke_token(lichess_token)

    # fetch_account validated the id shape (fail-closed LichessParseError,
    # mapped to 502 above); from here it is a trusted identity key.
    lichess_user_id = str(account["id"])

    service = AuthService(db)
    token, player, created = service.login_with_lichess(lichess_user_id, req.device_info)

    _ensure_lichess_link(db, player, account)

    auth_login_total.labels(result="lichess_success").inc()
    return {
        "access_token": token,
        "player_id": str(player.id),
        "token_type": "bearer",
        "created": created,
        "lichess_username": str(account.get("username") or lichess_user_id),
    }


@router.post("/logout")
def logout(
    authorization: str | None = Header(None),
    db: DBSession = Depends(get_db),
):
    # ``Header(None)`` (vs ``Header(...)``) so a missing Authorization
    # header surfaces as a 401 here rather than a Pydantic 422 before
    # the body runs — see AUTH_HDR_02 in test_auth_missing_header.py.
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    try:
        payload = decode_token(token)
    except jwt.InvalidTokenError:
        # See get_current_player above — narrow to jwt.InvalidTokenError
        # so non-JWT exceptions surface as 500 instead of masquerading
        # as bad-token 401s.
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    AuthService(db).logout(payload["session_id"])
    return {"status": "logged_out"}


def _serialise_player(player) -> dict:
    """Shared shape for GET /auth/me and PATCH /auth/me responses.

    Both endpoints return the full player profile so the Android client
    can replace its cached state from a single round-trip.  Pulled into
    a helper so the GET and PATCH paths can never drift in their
    response contract.
    """
    try:
        skill_vector = json.loads(player.skill_vector_json or "{}")
        skill_vector = {k: float(v) for k, v in skill_vector.items() if isinstance(v, (int, float))}
    except (ValueError, TypeError):
        skill_vector = {}
    return {
        "id": player.id,
        "email": player.email,
        "rating": player.rating,
        "confidence": player.confidence,
        "skill_vector": skill_vector,
        "training_xp": int(player.training_xp or 0),
    }


@router.get("/me")
def me(player=Depends(get_current_player)):
    return _serialise_player(player)


@router.patch("/me")
@limiter.limit("10/minute")
def update_me(
    req: UpdateMeRequest,
    request: Request,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Apply a partial update to the authenticated player's profile.

    Used by the Android Onboarding screen to forward the calibration
    estimate (rating + confidence) so the adaptation layer can dispatch
    a first opponent at the right level without waiting for the user
    to play enough games for rating drift to converge.

    Behaviour
    ---------
    * Empty request body (both fields ``None``) → 400.  Saves a wasted
      DB write and surfaces a malformed client immediately.
    * Either field ``None`` → leave the existing value intact.
    * Both fields present → update both.
    * Returns the same shape as GET /auth/me so the client can replace
      its cache from this single round-trip.
    """
    if req.rating is None and req.confidence is None:
        raise HTTPException(status_code=400, detail="No fields to update")
    if req.rating is not None:
        player.rating = req.rating
    if req.confidence is not None:
        player.confidence = req.confidence
    db.commit()
    db.refresh(player)
    return _serialise_player(player)


@router.post("/change-password")
@limiter.limit("5/minute")
def change_password(
    req: ChangePasswordRequest,
    request: Request,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    service = AuthService(db)
    try:
        service.change_password(player, req.current_password, req.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "updated"}
