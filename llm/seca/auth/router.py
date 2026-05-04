import json
import os
from fastapi import APIRouter, Depends, HTTPException, Header, Request, Response
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session, Session as DBSession
from llm.seca.shared_limiter import limiter

from .models import Base

# --- ensure ALL models are registered ---
from llm.seca.auth.models import *  # noqa: F401,F403
from llm.seca.events.models import *  # noqa: F401,F403
from llm.seca.brain.models import *  # noqa: F401,F403
from llm.seca.analytics.models import *  # noqa: F401,F403
from .service import AuthService
from .tokens import create_access_token, decode_token

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


def init_schema() -> None:
    """Create the SQLAlchemy schema and apply small SQLite-only migrations.

    Idempotent — safe to call from FastAPI lifespan, the test conftest
    fixture, or maintenance scripts.  Performs three ordered steps:

    1.  For SQLite, ensure ``data/`` exists so the file-based DB can be
        created on first connect.
    2.  ``Base.metadata.create_all`` — adds any missing tables.
    3.  ``ALTER TABLE players ADD COLUMN player_embedding`` — one-time
        column addition for legacy SQLite instances created before the
        column was added to the Player model.  Postgres deployments get
        the column from create_all() and skip this step.

    Must NOT run at module-import time: import-time DDL slows ``import
    llm.seca.auth.router`` (used by every backend test for its Pydantic
    schemas) and couples module loading to filesystem / DB I/O.
    """
    if _is_sqlite:
        os.makedirs("data", exist_ok=True)

    Base.metadata.create_all(bind=engine)

    if _is_sqlite:
        with engine.connect() as conn:
            rows = conn.execute(text("PRAGMA table_info(players)")).fetchall()
            if "player_embedding" not in {r[1] for r in rows}:
                conn.execute(
                    text("ALTER TABLE players ADD COLUMN player_embedding TEXT DEFAULT '[]'")
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
    response: Response,
    authorization: str = Header(...),
    db: DBSession = Depends(get_db),
):
    """Validate the Bearer token and return the matched Player.

    Side effect: on successful validation, attaches an
    ``X-Auth-Token`` response header containing a freshly-minted JWT
    for the same session.  Combined with the sliding session window
    in [AuthService.get_player_by_session], this gives the Android
    client a transparent refresh path so the JWT exp can stay tight
    (24h) without bouncing active users:
       - Active user: every authenticated call hands back a fresh
         24h JWT, the client saves it, the session slides forward.
       - Idle user: the JWT eventually expires; next call returns
         401 and the client routes to login.

    The new token is NOT issued on the failure paths — an attacker
    probing with a stolen-then-revoked token must not receive a
    fresh JWT they could keep using.
    """
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    service = AuthService(db)
    player = service.get_player_by_session(payload["session_id"], token)

    if not player:
        raise HTTPException(status_code=401, detail="Session invalid")

    # Mint a fresh JWT for this session and hand it back so the
    # client can rotate its stored token transparently.
    new_token = create_access_token(
        player_id=str(player.id),
        session_id=payload["session_id"],
    )
    response.headers["X-Auth-Token"] = new_token
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
        raise HTTPException(status_code=400, detail="Registration failed")
    token, _ = service.login(req.email, req.password, device_info="register")
    return {
        "access_token": token,
        "player_id": str(player.id),
        "token_type": "bearer",
    }


@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, req: LoginRequest, db: Session = Depends(get_db)):
    service = AuthService(db)
    try:
        token, player = service.login(req.email, req.password, req.device_info)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {
        "access_token": token,
        "player_id": str(player.id),
        "token_type": "bearer",
    }


@router.post("/logout")
def logout(
    authorization: str = Header(...),
    db: DBSession = Depends(get_db),
):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    try:
        payload = decode_token(token)
    except Exception:
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
