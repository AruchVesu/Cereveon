import asyncio
import json
import logging
import os
import re
import shutil
import chess
import time
import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from llm.seca.shared_limiter import limiter
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator

try:
    from .player_api import router as player_router
except ImportError:
    # Supports top-level module execution (e.g. `uvicorn server:app`)
    from player_api import router as player_router
from llm.seca.auth.router import (
    router as auth_router,
    get_current_player,
    init_schema as init_auth_schema,
)
from llm.seca.auth.api_key import verify_api_key
from llm.seca.events.router import router as game_router
from llm.seca.curriculum.router import router as curriculum_router
from llm.seca.inference.router import router as inference_router
from llm.seca.analytics.router import router as analytics_router

# register SECA models
import llm.seca.events.models

from llm.seca.engines.stockfish.pool import (
    EnginePoolSettings,
    FenMoveCache,
    StockfishEnginePool,
)
from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.explain_pipeline import generate_validated_explanation
from llm.rag.validators.explain_response_schema import (
    validate_explain_response,
    validate_chat_response,
    validate_live_move_response,
    ExplainSchemaError,
)
from llm.rag.prompts.input_sanitizer import sanitize_user_query
from llm.seca.learning.outcome_tracker import ExplanationOutcomeTracker
from llm.seca.learning.skill_update import SkillState
from llm.seca.adaptation.coupling import compute_adaptation
from llm.seca.adaptation.dynamic_mode import DynamicModeRegistry
from llm.seca.curriculum.scheduler import CurriculumScheduler
from llm.seca.curriculum.types import Weakness
from llm.seca.storage.db import init_db
from llm.seca.world_model.safe_stub import SafeWorldModel
from llm.seca.explainer.safe_explainer import SafeExplainer
from llm.seca.safety.freeze import enforce
from llm.seca.runtime.safe_mode import SAFE_MODE
from llm.seca.coach.chat_pipeline import (
    generate_chat_reply,
    ChatTurn as _ChatPipelineTurn,
)
from llm.seca.coach.live_move_pipeline import generate_live_reply
from llm.seca.storage.repo import (
    checkpoint_game,
    create_game,
    delete_opening,
    get_active_game,
    get_or_create_auto_game,
    list_repertoire,
    log_move,
    log_explanation,
    seed_default_repertoire,
    set_active_opening,
    update_learning_score,
    update_opening_mastery,
    upsert_opening,
)

logger = logging.getLogger(__name__)
logger.info("Running server from: %s", __file__)
logger.info("SECA safe_mode=%s", SAFE_MODE)

load_dotenv()

API_KEY = os.getenv("SECA_API_KEY")
ENV = os.getenv("SECA_ENV", "dev")
IS_PROD = ENV in {"prod", "production"}
DEBUG = not IS_PROD

if IS_PROD and API_KEY is None:
    raise RuntimeError(
        "SECA_API_KEY env var is required in production (SECA_ENV=prod). "
        "Set a non-empty value before starting the server."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine_pool, move_cache, scheduler
    global world_model, async_predict_enabled, async_predict_plies, async_predict_movetime_ms
    try:
        init_db()
        # SQLAlchemy schema + small SQLite-only migrations.  Moved out of
        # auth/router.py module-import time so importing the router (e.g.
        # to access Pydantic request models in tests) no longer pays the
        # cost of opening the DB and running DDL.
        init_auth_schema()
        world_model = SafeWorldModel()
        enforce(world_model)
        if os.name == "nt":
            default_stockfish_path = "engines/stockfish.exe"
        else:
            default_stockfish_path = shutil.which("stockfish") or "/usr/games/stockfish"
        stockfish_path = os.getenv("STOCKFISH_PATH", default_stockfish_path)
        settings = EnginePoolSettings(
            stockfish_path=stockfish_path,
            pool_size=max(1, _env_int("ENGINE_POOL_SIZE", 8)),
            threads=max(1, _env_int("ENGINE_THREADS", 1)),
            hash_mb=max(16, _env_int("ENGINE_HASH_MB", 128)),
            skill_level=_env_int("ENGINE_SKILL_LEVEL", 10),
            default_movetime_ms=max(20, _env_int("ENGINE_DEFAULT_MOVETIME_MS", 40)),
            training_movetime_ms=max(20, _env_int("ENGINE_TRAINING_MOVETIME_MS", 40)),
            analysis_movetime_ms=max(
                20,
                _env_int_first(
                    ["ENGINE_ANALYSIS_MOVETIME_MS", "ENGINE_DEEP_MOVETIME_MS"],
                    80,
                ),
            ),
            blitz_movetime_ms=max(20, _env_int("ENGINE_BLITZ_MOVETIME_MS", 25)),
            queue_timeout_ms=max(1, _env_int("ENGINE_QUEUE_TIMEOUT_MS", 50)),
        )
        engine_pool = StockfishEnginePool(settings)
        engine_pool.startup()
        move_cache = FenMoveCache(
            redis_url=os.getenv("REDIS_URL"),
            ttl_seconds=_env_int("MOVE_CACHE_TTL_SECONDS", 3600),
            max_memory_items=max(1, _env_int("MOVE_CACHE_L1_MAX_ITEMS", 500)),
        )
        async_predict_enabled = _env_bool("ENGINE_ASYNC_PREDICT_ENABLED", True)
        async_predict_plies = max(0, _env_int("ENGINE_ASYNC_PREDICT_PLIES", 2))
        async_predict_movetime_ms = max(
            20,
            _env_int("ENGINE_ASYNC_PREDICT_MOVETIME_MS", 20),
        )
        prewarm_fens = _env_fens("ENGINE_PREWARM_FENS")
        prewarm_modes = _env_csv("ENGINE_PREWARM_MODES", "blitz")
        if prewarm_fens and prewarm_modes:
            warmed = 0
            for mode in prewarm_modes:
                warmed += engine_pool.prewarm_cache(
                    move_cache=move_cache,
                    fens=prewarm_fens,
                    mode=mode,
                )
            logger.info(
                "Move cache prewarmed (entries=%d, positions=%d, modes=%s)",
                warmed,
                len(prewarm_fens),
                ",".join(prewarm_modes),
            )
        scheduler = CurriculumScheduler()
        logger.info("DB initialized")
        logger.info("Stockfish engine pool initialized (size=%d)", settings.pool_size)
    except Exception as e:
        if engine_pool:
            engine_pool.close()
        engine_pool = None
        move_cache = None
        logger.error("Stockfish engine pool DISABLED: %s", e)

    yield

    if engine_pool:
        engine_pool.close()
        logger.info("Stockfish engine pool closed")


app = FastAPI(title="SECA Chess Coach API", lifespan=lifespan)
app.state.limiter = limiter

# ---- CORS ----------------------------------------------------------------
# Dev contributors hitting the API from a browser, the Android emulator, or
# Vite/webpack dev servers on the same machine should not need to set
# CORS_ALLOWED_ORIGINS by hand.  In dev we default to the common local
# development origins.  In prod the env var must be set explicitly — fail
# loud at startup, mirroring the SECA_API_KEY / SECRET_KEY pattern, so a
# misconfigured deployment never silently blocks every browser request.
DEV_CORS_DEFAULTS = [
    "http://localhost:8000",  # bare-metal API on dev host
    "http://127.0.0.1:8000",  # ditto, IPv4 form
    "http://10.0.2.2:8000",  # Android emulator → host loopback
    "http://localhost:3000",  # common Vite/Next/CRA dev server
    "http://localhost:5173",  # Vite default
]

_cors_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
if not _cors_origins:
    if IS_PROD:
        raise RuntimeError(
            "CORS_ALLOWED_ORIGINS env var is required in production "
            "(SECA_ENV=prod).  Set a comma-separated list of allowed origins, "
            "e.g. https://app.example.com"
        )
    _cors_origins = list(DEV_CORS_DEFAULTS)
    logger.info(
        "CORS_ALLOWED_ORIGINS unset; using dev defaults (%d origins).  "
        "Set the env var to override.",
        len(_cors_origins),
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Api-Key"],
)

# ---- Request body size limit (512 KB) ------------------------------------
_MAX_BODY_BYTES = 512 * 1024


_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


class _LimitBodySize(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is None:
            # Chunked-encoded or header-less POST/PUT/PATCH bodies bypass a
            # header-only size check entirely.  Require Content-Length for all
            # body-carrying methods so the limit is always enforceable.
            if request.method in _BODY_METHODS:
                return JSONResponse(
                    status_code=411, content={"error": "Content-Length header required"}
                )
        else:
            try:
                if int(cl) > _MAX_BODY_BYTES:
                    return JSONResponse(
                        status_code=413, content={"error": "Request body too large"}
                    )
            except ValueError:
                return JSONResponse(status_code=400, content={"error": "Invalid Content-Length"})
        return await call_next(request)


app.add_middleware(_LimitBodySize)


# ---- Security response headers -------------------------------------------
# Defense-in-depth response headers.  This API serves JSON only — CSP and
# Permissions-Policy are precautions in case any future error path or
# misbehaving middleware emits HTML; with default-src 'none' no script,
# frame, or sub-resource can execute even if the body is rendered, and the
# Permissions-Policy block keeps every sensitive browser feature disabled.
_CSP_HEADER = (
    "default-src 'none'; " "frame-ancestors 'none'; " "base-uri 'none'; " "form-action 'none'"
)
_PERMISSIONS_POLICY_HEADER = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), payment=(), usb=()"
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = _CSP_HEADER
    response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY_HEADER
    return response


# Allow-list of methods clients may legitimately request via the
# X-HTTP-Method-Override header.  Only PATCH today; DELETE / PUT could
# follow if a future client ever needs them.  Anything outside this set
# is silently ignored so an attacker can't elevate a POST to e.g. an
# arbitrary method that bypasses CSRF protections elsewhere.
_METHOD_OVERRIDE_ALLOWED = frozenset({"PATCH"})


@app.middleware("http")
async def http_method_override(request: Request, call_next):
    """Promote POST → PATCH (or other allow-listed methods) when the
    client sets ``X-HTTP-Method-Override``.

    The Android client's HTTP layer uses ``java.net.HttpURLConnection``
    which on JDK 17 (and earlier) does not allow PATCH as a request
    method.  Rather than ship a reflection hack on the client or pull
    in OkHttp at runtime just for this one endpoint, we accept POST +
    header at the edge.  Production Android may also use real PATCH —
    this middleware is a no-op when the header is absent.

    Restricted to the allow-list above so the override cannot be used
    to escape the documented HTTP method semantics of any other route.
    """
    if request.method == "POST":
        override = request.headers.get("x-http-method-override", "").upper()
        if override in _METHOD_OVERRIDE_ALLOWED:
            request.scope["method"] = override
    return await call_next(request)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"error": "Too many requests"})


DEFAULT_PREWARM_FENS = [
    chess.STARTING_FEN,
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",  # 1.e4 e5
    "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2",  # 1.d4 d5
    "r1bqkbnr/pppp1ppp/2n5/4p3/3PP3/5N2/PPP2PPP/RNBQKB1R b KQkq - 2 3",
    "r2q1rk1/pp2bppp/2n1bn2/2pp4/3P4/2PBPN2/PP1N1PPP/R1BQ1RK1 w - - 0 9",
]


app.include_router(player_router)
app.include_router(auth_router)
app.include_router(game_router)
app.include_router(curriculum_router)
app.include_router(analytics_router)
app.include_router(
    inference_router,
    prefix="/seca",
    tags=["seca-inference"],
    dependencies=[Depends(verify_api_key)],
)
tracker = ExplanationOutcomeTracker()
player_skill_memory: dict[str, SkillState] = {}
scheduler: CurriculumScheduler | None = None
world_model: SafeWorldModel | None = None
safe_explainer = SafeExplainer()
_dynamic_registry = DynamicModeRegistry()

# ------------------------------------------------------------------
# Engine lifecycle
# ------------------------------------------------------------------

engine_pool: StockfishEnginePool | None = None
move_cache: FenMoveCache | None = None
move_stats = {"total": 0, "cache_hits": 0}
move_stats_lock = threading.Lock()
async_predict_enabled = True
async_predict_plies = 2
async_predict_movetime_ms = 20


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int_first(names: list[str], default: int) -> int:
    for name in names:
        raw = os.getenv(name)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return default


def _env_csv(name: str, default_csv: str) -> list[str]:
    raw = os.getenv(name, default_csv)
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def _normalize_fen(fen: str) -> str:
    if fen.strip().lower() == "startpos":
        return chess.STARTING_FEN
    return fen


def _cache_line_key(moves_uci: list[str] | None) -> str | None:
    if not moves_uci:
        return None
    return moves_uci[-1]


def _record_move_stat(cache_hit: bool) -> float:
    with move_stats_lock:
        move_stats["total"] += 1
        if cache_hit:
            move_stats["cache_hits"] += 1
        if move_stats["total"] == 0:
            return 0.0
        return move_stats["cache_hits"] / move_stats["total"]


def _env_fens(name: str) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return list(DEFAULT_PREWARM_FENS)
    return [_normalize_fen(part.strip()) for part in raw.split("||") if part.strip()]


@lru_cache(maxsize=4096)
def _fen_board(fen: str) -> chess.Board:
    return chess.Board(_normalize_fen(fen))


def _board_from_payload(fen: str, moves_uci: list[str] | None) -> chess.Board:
    normalized_fen = _normalize_fen(fen)
    board = _fen_board(normalized_fen).copy(stack=False)
    if not moves_uci:
        return board

    candidate = chess.Board()
    try:
        for move_uci in moves_uci:
            candidate.push_uci(move_uci)
        if candidate.fen() == normalized_fen:
            return candidate
    except ValueError:
        return board

    return board


def _predictive_cache_followups(
    *,
    seed_fen: str,
    mode: str,
    target_elo: int | None,
) -> None:
    if not async_predict_enabled or engine_pool is None or move_cache is None:
        return

    try:
        board = chess.Board(seed_fen)
    except ValueError:
        return

    line_key: str | None = None
    for _ in range(max(0, async_predict_plies)):
        if board.is_game_over():
            return

        cached = move_cache.get(
            fen=board.fen(),
            mode=mode,
            movetime_ms=async_predict_movetime_ms,
            target_elo=target_elo,
            line_key=line_key,
        )
        if cached:
            try:
                mv = chess.Move.from_uci(cached)
                if mv in board.legal_moves:
                    board.push(mv)
                    line_key = mv.uci()
                    continue
            except ValueError:
                pass

        try:
            mv = engine_pool.select_move(
                fen=board.fen(),
                board=board,
                mode=mode,
                movetime_ms=async_predict_movetime_ms,
                queue_timeout_ms=25,
                target_elo=target_elo,
            )
            move_cache.set(
                fen=board.fen(),
                mode=mode,
                movetime_ms=async_predict_movetime_ms,
                target_elo=target_elo,
                move_uci=mv.uci(),
                line_key=line_key,
            )
            board.push(mv)
            line_key = mv.uci()
        except Exception:
            return


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------

_VALID_MODES = {"default", "blitz", "analysis", "training"}


def _validate_fen_field(v: str) -> str:
    stripped = v.strip()
    if stripped.lower() == "startpos":
        return v
    parts = stripped.split()
    if len(parts) != 6 or len(stripped) > 100:
        raise ValueError("invalid FEN")
    try:
        chess.Board(stripped)
    except ValueError:
        raise ValueError("invalid FEN")
    return v


_MOVES_UCI_MAX_ENTRIES = 500
_MOVES_UCI_MAX_ELEMENT_LEN = 5
_UCI_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbnQRBN]?$")


class MoveRequest(BaseModel):
    fen: str
    moves_uci: list[str] | None = None
    mode: str | None = "default"
    movetime_ms: int | None = None

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        return _validate_fen_field(v)

    @field_validator("moves_uci")
    @classmethod
    def validate_moves_uci(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            if len(v) > _MOVES_UCI_MAX_ENTRIES:
                raise ValueError(f"moves_uci too many entries (max {_MOVES_UCI_MAX_ENTRIES})")
            for move in v:
                if len(move) > _MOVES_UCI_MAX_ELEMENT_LEN:
                    raise ValueError(
                        f"moves_uci element too long (max {_MOVES_UCI_MAX_ELEMENT_LEN} chars)"
                    )
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str | None) -> str | None:
        if v is not None and v.lower() not in _VALID_MODES:
            raise ValueError(f"mode must be one of {_VALID_MODES}")
        return v

    @field_validator("movetime_ms")
    @classmethod
    def validate_movetime(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 60_000):
            raise ValueError("movetime_ms must be 1–60000")
        return v


class AnalyzeRequest(BaseModel):
    fen: str
    stockfish_json: dict | None = None
    user_query: str | None = ""

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        return _validate_fen_field(v)

    @field_validator("stockfish_json")
    @classmethod
    def validate_stockfish_json(cls, v: dict | None) -> dict | None:
        if v is not None:
            if len(v) > 50:
                raise ValueError("stockfish_json too many keys (max 50)")
            for val in v.values():
                if isinstance(val, dict) and len(val) > 50:
                    raise ValueError("stockfish_json nested dict too many keys (max 50)")
        return v

    @field_validator("user_query")
    @classmethod
    def validate_user_query(cls, v: str | None) -> str | None:
        if v and len(v) > 2000:
            raise ValueError("user_query too long (max 2000 chars)")
        return sanitize_user_query(v) if v else v


class LiveMoveRequest(BaseModel):
    fen: str
    uci: str
    player_id: str | None = None

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        return _validate_fen_field(v)

    @field_validator("uci")
    @classmethod
    def validate_uci(cls, v: str) -> str:
        if not re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbnQRBN]?", v):
            raise ValueError(
                "uci move must be [a-h][1-8][a-h][1-8] with optional promotion [qrbnQRBN]"
            )
        return v

    @field_validator("player_id")
    @classmethod
    def validate_player_id(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 100:
            raise ValueError("player_id too long (max 100 chars)")
        return v


class StartGameRequest(BaseModel):
    # T3: player_id is now derived from the authenticated session.  The field
    # is accepted (optional) for backwards compatibility with older Android
    # clients that still send it, and ignored server-side.  Remove the field
    # once all clients have been updated to omit it.
    player_id: str | None = None

    @field_validator("player_id")
    @classmethod
    def validate_player_id(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 100:
            raise ValueError("player_id too long (max 100 chars)")
        return v


class OutcomeRequest(BaseModel):
    explanation_id: str
    moves_analyzed: int
    avg_cpl: float
    blunder_rate: float
    tactic_success: bool
    confidence_delta: float

    @field_validator("explanation_id")
    @classmethod
    def validate_explanation_id(cls, v: str) -> str:
        if len(v) > 200:
            raise ValueError("explanation_id too long (max 200 chars)")
        return v

    @field_validator("moves_analyzed")
    @classmethod
    def validate_moves_analyzed(cls, v: int) -> int:
        if not (0 <= v <= 10_000):
            raise ValueError("moves_analyzed must be 0–10000")
        return v

    @field_validator("avg_cpl")
    @classmethod
    def validate_avg_cpl(cls, v: float) -> float:
        if not (-3_000.0 <= v <= 3_000.0):
            raise ValueError("avg_cpl must be in [-3000, 3000]")
        return v

    @field_validator("blunder_rate")
    @classmethod
    def validate_blunder_rate(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("blunder_rate must be in [0.0, 1.0]")
        return v

    @field_validator("confidence_delta")
    @classmethod
    def validate_confidence_delta(cls, v: float) -> float:
        if not (-1.0 <= v <= 1.0):
            raise ValueError("confidence_delta must be in [-1.0, 1.0]")
        return v


class CurriculumRecommendRequest(BaseModel):
    skill_vector: list[float]


class GameRequest(BaseModel):
    player_id: str
    pgn: str


class GameFinishRequest(BaseModel):
    player_id: str
    pgn: str


class GameFinishClosedLoopRequest(BaseModel):
    player_id: int
    game_id: int


class AdaptationModeRequest(BaseModel):
    """Request body for POST /adaptation/mode."""

    enabled: bool
    base_elo: int | None = None

    @field_validator("base_elo")
    @classmethod
    def validate_base_elo(cls, v: int | None) -> int | None:
        from llm.seca.adaptation.dynamic_mode import ELO_MIN, ELO_MAX

        if v is not None and not (ELO_MIN <= v <= ELO_MAX):
            raise ValueError(f"base_elo must be in [{ELO_MIN}, {ELO_MAX}]")
        return v


class ChatTurnModel(BaseModel):
    """A single turn in a coaching conversation."""

    role: Literal["user", "assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        if len(v) > 2000:
            raise ValueError("message content too long (max 2000 chars)")
        return sanitize_user_query(v) if v else v


class ChatRequest(BaseModel):
    """Request body for POST /chat."""

    fen: str
    messages: list[ChatTurnModel]
    player_profile: dict | None = None
    past_mistakes: list[str] | None = None
    # Coach voice setting from the Android Settings sheet — affects
    # the LLM's tone but never its content (the engine signal stays
    # authoritative).  Strict allow-list so an unknown value fails
    # validation rather than silently bleeding into the prompt.
    coach_voice: str | None = None

    @field_validator("player_profile")
    @classmethod
    def validate_player_profile(cls, v: dict | None) -> dict | None:
        if v is not None:
            if len(v) > 20:
                raise ValueError("player_profile too many keys (max 20)")
            total = sum(len(str(k)) + len(str(val)) for k, val in v.items())
            if total > 2000:
                raise ValueError("player_profile too large (max 2000 chars total)")
            for k, val in v.items():
                if isinstance(k, str):
                    sanitize_user_query(k)
                if isinstance(val, str):
                    sanitize_user_query(val)
        return v

    move_count: int | None = None

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        return _validate_fen_field(v)

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, v: list) -> list:
        if len(v) > 50:
            raise ValueError("too many messages in history (max 50)")
        return v

    @field_validator("past_mistakes")
    @classmethod
    def validate_past_mistakes(cls, v: list | None) -> list | None:
        if v is not None:
            if len(v) > 20:
                raise ValueError("past_mistakes list too long (max 20)")
            for item in v:
                if len(item) > 500:
                    raise ValueError("past_mistakes item too long (max 500 chars)")
                sanitize_user_query(item)
        return v

    @field_validator("move_count")
    @classmethod
    def validate_move_count(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 10_000):
            raise ValueError("move_count must be 0–10000")
        return v

    @field_validator("coach_voice")
    @classmethod
    def validate_coach_voice(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if v == "":
            return None
        # Allow-list mirrors the Android SettingsBottomSheet radio
        # values exactly.  Any other value (e.g. attacker-supplied
        # prompt-injection bait disguised as a tone) is rejected
        # before it reaches the LLM prompt.
        if v not in {"formal", "conversational", "terse"}:
            raise ValueError("coach_voice must be one of 'formal', 'conversational', 'terse'")
        return v


def build_engine_signal(req: AnalyzeRequest):
    return extract_engine_signal(req.stockfish_json, fen=req.fen)


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------


@app.get("/")
def root():
    """Root liveness probe for load-balancers and uptime monitors.

    No authentication required.  Returns the same body as GET /health so
    monitoring tools that check the root path work without extra config.
    """
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/seca/status")
def seca_status():
    """Return the SECA runtime safety flag.

    Open endpoint (no auth): readable by Android at cold-start so the
    client can confirm ``safe_mode`` is active before sending coaching
    requests.  Always ``safe_mode: true`` in the current release;
    bandit training and neural policy updates are hard-disabled via
    ``SAFE_MODE = True`` in ``llm/seca/runtime/safe_mode.py`` and
    enforced at startup by ``llm/seca/safety/freeze.py``.

    Response is intentionally minimal — the previous shape exposed
    ``bandit_enabled`` (redundant; just ``not safe_mode``) and
    ``version`` ("1.0", unused by any client decision), both of which
    were small information-disclosure surfaces with no compensating
    use case.
    """
    return {"safe_mode": SAFE_MODE}


@app.get("/debug/engine")
def engine_debug(_: None = Depends(verify_api_key)):
    if engine_pool is None:
        return {"pool_size": 0}
    return {"pool_size": engine_pool.qsize()}


# ------------------------------------------------------------------
# Dynamic adaptation mode
# ------------------------------------------------------------------


@app.post("/adaptation/mode")
@limiter.limit("30/minute")
def set_adaptation_mode(
    req: AdaptationModeRequest,
    request: Request,
    player=Depends(get_current_player),
):
    """Enable or disable dynamic adaptation mode for the authenticated player.

    When enabled the engine's target ELO shifts each move based on observed
    move quality, converging toward the player's actual skill level.  Use this
    during first-play / skill-assessment sessions.

    Request body::

        {"enabled": true, "base_elo": 1200}   # base_elo optional

    If ``base_elo`` is omitted the player's current computed adaptation ELO is
    used as the starting point.
    """
    base_elo = req.base_elo
    if base_elo is None and req.enabled:
        adaptation = compute_adaptation(float(player.rating), float(player.confidence))
        base_elo = adaptation["opponent"]["target_elo"]

    state = _dynamic_registry.set_mode(
        str(player.id),
        enabled=req.enabled,
        base_elo=base_elo,
    )
    return {
        "enabled": state.enabled,
        "current_elo": state.current_elo,
        "move_count": state.move_count,
    }


@app.get("/adaptation/mode")
def get_adaptation_mode(player=Depends(get_current_player)):
    """Return the current dynamic adaptation state for the authenticated player."""
    state = _dynamic_registry.get_state(str(player.id))
    return {
        "enabled": state.enabled,
        "current_elo": state.current_elo,
        "move_count": state.move_count,
    }


# ------------------------------------------------------------------
# Move endpoint (pooled stockfish)
# ------------------------------------------------------------------


@app.post("/move")
@limiter.limit("30/minute")
def move(
    req: MoveRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    player=Depends(get_current_player),
):
    request_started = time.perf_counter()
    if engine_pool is None:
        return {"error": "engine pool unavailable"}

    normalized_fen = _normalize_fen(req.fen)
    board = _board_from_payload(normalized_fen, req.moves_uci)
    adaptation = compute_adaptation(player.rating, player.confidence)
    target_elo = adaptation["opponent"]["target_elo"]
    dynamic_elo = _dynamic_registry.get_elo(str(player.id))
    if dynamic_elo is not None:
        target_elo = dynamic_elo

    mode = (req.mode or "default").lower()
    resolved_movetime_ms = engine_pool.resolve_movetime_ms(mode, req.movetime_ms)
    line_key = _cache_line_key(req.moves_uci)

    cache_hit = False
    fallback_used = False
    engine_time_ms = 0.0
    mv: chess.Move | None = None

    if move_cache:
        cached_uci = move_cache.get(
            fen=normalized_fen,
            mode=mode,
            movetime_ms=resolved_movetime_ms,
            target_elo=target_elo,
            line_key=line_key,
        )
        if cached_uci:
            try:
                candidate = chess.Move.from_uci(cached_uci)
                if candidate in board.legal_moves:
                    mv = candidate
                    cache_hit = True
            except ValueError:
                mv = None

    if cache_hit and mv is not None:
        san = board.san(mv)
        cache_hit_rate = _record_move_stat(cache_hit=True)
        latency_ms = round((time.perf_counter() - request_started) * 1000.0, 2)
        return {
            "uci": mv.uci(),
            "san": san,
            "opponent_elo": target_elo,
            "mode": mode,
            "movetime_ms": resolved_movetime_ms,
            "cache_hit": True,
            "fallback_used": False,
            "telemetry": {
                "latency_ms": latency_ms,
                "engine_time_ms": 0.0,
                "cache_hit_rate": round(cache_hit_rate, 4),
                "queue_depth": engine_pool.qsize(),
            },
        }

    try:
        engine_started = time.perf_counter()
        mv = engine_pool.select_move(
            fen=normalized_fen,
            board=board,
            moves_uci=req.moves_uci,
            mode=mode,
            movetime_ms=resolved_movetime_ms,
            target_elo=target_elo,
        )
        engine_time_ms = round((time.perf_counter() - engine_started) * 1000.0, 2)
    except RuntimeError:
        mv = engine_pool.fast_fallback_move(board)
        fallback_used = True
        engine_time_ms = round((time.perf_counter() - request_started) * 1000.0, 2)

    if move_cache and not fallback_used:
        move_cache.set(
            fen=normalized_fen,
            mode=mode,
            movetime_ms=resolved_movetime_ms,
            target_elo=target_elo,
            move_uci=mv.uci(),
            line_key=line_key,
        )

    san = board.san(mv)
    ply = board.fullmove_number * 2 - (0 if board.turn else 1)
    log_move(
        game_id=get_or_create_auto_game(str(player.id)),
        ply=ply,
        fen=normalized_fen,
        uci=mv.uci(),
        san=san,
        eval=None,
    )
    board_after = board.copy(stack=False)
    board_after.push(mv)
    if async_predict_enabled and not fallback_used:
        background_tasks.add_task(
            _predictive_cache_followups,
            seed_fen=board_after.fen(),
            mode=mode,
            target_elo=target_elo,
        )
    cache_hit_rate = _record_move_stat(cache_hit=cache_hit)
    latency_ms = round((time.perf_counter() - request_started) * 1000.0, 2)

    return {
        "uci": mv.uci(),
        "san": san,
        "opponent_elo": target_elo,
        "mode": mode,
        "movetime_ms": resolved_movetime_ms,
        "cache_hit": cache_hit,
        "fallback_used": fallback_used,
        "telemetry": {
            "latency_ms": latency_ms,
            "engine_time_ms": engine_time_ms,
            "cache_hit_rate": round(cache_hit_rate, 4),
            "queue_depth": engine_pool.qsize(),
        },
    }


# ------------------------------------------------------------------
# Live move endpoint (realtime coaching)
# ------------------------------------------------------------------


@app.post("/live/move")
@limiter.limit("30/minute")
async def live_move(
    req: LiveMoveRequest,
    request: Request,
    player=Depends(get_current_player),
):
    """Mode-1: per-move coaching feedback after the human's move.

    LLM-powered (1-2 sentences); falls back to deterministic hint when
    Ollama is unavailable.  Runs in a thread-pool executor so the async
    event loop is not blocked during the Ollama HTTP call.
    """
    adaptation = compute_adaptation(player.rating, player.confidence)
    result = await asyncio.to_thread(
        generate_live_reply,
        req.fen,
        req.uci,
        str(player.id),
        adaptation["teaching"]["style"],
    )
    if _dynamic_registry.get_state(str(player.id)).enabled:
        _dynamic_registry.record_move_quality(str(player.id), result.move_quality)
    response = {
        "status": "ok",
        "hint": result.hint,
        "engine_signal": result.engine_signal,
        "move_quality": result.move_quality,
        "mode": result.mode,
        "dynamic_adaptation": _dynamic_registry.get_state(str(player.id)).enabled,
    }
    validate_live_move_response(response)
    return response


# ------------------------------------------------------------------
# Analyze endpoint (engine signal only)
# ------------------------------------------------------------------


@app.post("/analyze")
@limiter.limit("30/minute")
def analyze(req: AnalyzeRequest, request: Request, _: None = Depends(verify_api_key)):
    return {"engine_signal": build_engine_signal(req)}


@app.get("/next-training/{player_id}")
def next_training(player_id: str, player=Depends(get_current_player)):
    if len(player_id) > 100:
        raise HTTPException(status_code=422, detail="player_id too long (max 100 chars)")
    if player_id != str(player.id):
        raise HTTPException(
            status_code=403,
            detail="Cannot access another player's training",
        )
    skill = player_skill_memory.get(player_id, SkillState())

    # demo weaknesses (later from analyzer)
    weaknesses = [
        Weakness("tactics", severity=0.7, confidence=0.9),
        Weakness("endgame", severity=0.4, confidence=0.8),
    ]

    task = scheduler.next_task(weaknesses, skill.rating)

    return {
        "topic": task.topic,
        "difficulty": task.difficulty,
        "format": task.format,
        "expected_gain": task.expected_gain,
    }


@app.post("/game/start")
@limiter.limit("20/minute")
def start_game(req: StartGameRequest, request: Request, player=Depends(get_current_player)):
    # T3: player_id is sourced from the JWT, not the request body.  Any
    # req.player_id sent by older clients is ignored (see StartGameRequest).
    game_id = create_game(str(player.id))
    return {"game_id": game_id}


# ---------------------------------------------------------------------------
# Cross-device resume — checkpoint + active-game query
# ---------------------------------------------------------------------------
#
# These endpoints back the cross-device resume feature: the client
# persists its in-progress board state server-side via /game/{id}/
# checkpoint after each move, and pulls the most recent unfinished
# game's state via /game/active at cold-start when the local
# SharedPreferences snapshot is missing (e.g. fresh install on a
# second device).
#
# Reuses the same `games` table /game/start writes to — see
# storage/schema.sql + storage/db.py for the schema.


class GameCheckpointRequest(BaseModel):
    """In-progress board state pushed by the client after each move.

    fen: full FEN of the current position.  Bounded at 256 chars
        (a real FEN tops out around 90; the cap rejects abuse).
    uci_history: comma-separated UCI moves (e.g. "e2e4,e7e5,g1f3").
        Bounded at 16 KB — enough for a 2000-move game which is
        well beyond any realistic length.
    """

    fen: str
    uci_history: str = ""

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("fen must not be empty")
        if len(v) > 256:
            raise ValueError("fen too long (max 256 chars)")
        for ch in v:
            if ord(ch) < 0x20 or ord(ch) == 0x7F:
                raise ValueError("fen contains control characters")
        return v

    @field_validator("uci_history")
    @classmethod
    def validate_uci_history(cls, v: str) -> str:
        if len(v) > 16_384:
            raise ValueError("uci_history too long (max 16384 chars)")
        for ch in v:
            if ord(ch) < 0x20 or ord(ch) == 0x7F:
                raise ValueError("uci_history contains control characters")
        return v


@app.post("/game/{game_id}/checkpoint")
@limiter.limit("60/minute")
def checkpoint_game_state(
    game_id: str,
    req: GameCheckpointRequest,
    request: Request,
    player=Depends(get_current_player),
):
    """Persist the current in-progress state for [game_id].

    Authorization is enforced by the games-table query inside
    repo.checkpoint_game (only updates when finished_at IS NULL).
    Cross-player attempts return 404 because the row matches no
    unfinished game owned by this player.

    Rate limit is 60/minute — generous enough that a fast-paced
    game (~one checkpoint per move) never trips it, but bounded
    against accidental tight-loop spam from a buggy client.
    """
    # Defensive: cap game_id at 64 chars to match GameFinishRequest's
    # game_id validator.  Path params don't go through pydantic
    # validation by default.
    if len(game_id) > 64:
        raise HTTPException(status_code=400, detail="game_id too long")
    for ch in game_id:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise HTTPException(status_code=400, detail="game_id contains control characters")

    # Verify the game belongs to this player BEFORE checkpointing —
    # repo.checkpoint_game only filters by finished_at, not player.
    # A stricter check here means a stolen game_id can't be hijacked
    # to overwrite another player's checkpoint.
    active = get_active_game(str(player.id))
    if active is None or active["game_id"] != game_id:
        # Look up by id directly to distinguish "wrong owner" (403)
        # from "doesn't exist / already finished" (404).
        from llm.seca.storage.db import get_conn

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT player_id, finished_at FROM games WHERE id = ?",
                (game_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise HTTPException(status_code=404, detail="game not found")
        owner_id, finished_at = row
        if owner_id != str(player.id):
            raise HTTPException(status_code=403, detail="not your game")
        if finished_at is not None:
            raise HTTPException(status_code=409, detail="game already finished")

    if not checkpoint_game(game_id, req.fen, req.uci_history):
        # Race: row was finished between the ownership check and now.
        raise HTTPException(status_code=409, detail="game already finished")
    return {"status": "checkpointed"}


@app.get("/game/active")
@limiter.limit("60/minute")
def active_game(request: Request, player=Depends(get_current_player)):
    """Return the player's most recent unfinished game with a
    checkpoint, or 404 when there isn't one (= "no resumable game").

    Used by the Android client at cold-start when the local
    SharedPreferences snapshot is missing (fresh install / device
    swap).  Combined with the local snapshot, this lets a user pick
    up exactly where they left off across devices.
    """
    state = get_active_game(str(player.id))
    if state is None:
        raise HTTPException(status_code=404, detail="no active game")
    return state


# ---------------------------------------------------------------------------
# Opening repertoire (backs AtriumOpenings)
# ---------------------------------------------------------------------------


# Canonical default repertoire — handed back when a player has nothing
# stored so AtriumOpenings reads as a populated screen on first visit.
# Mirrors OpeningsActivity.DEFAULT_REPERTOIRE 1-for-1 (drift here would
# show the user different defaults on first vs. subsequent visits).
DEFAULT_REPERTOIRE: list[dict] = [
    {
        "eco": "C84",
        "name": "Ruy Lopez · Closed",
        "line": "1.e4 e5 2.♘f3 ♘c6 3.♗b5 a6",
        "mastery": 0.78,
        "is_active": True,
        "ordinal": 0,
    },
    {
        "eco": "B22",
        "name": "Sicilian · Alapin",
        "line": "1.e4 c5 2.c3 ♘f6 3.e5 ♘d5",
        "mastery": 0.55,
        "is_active": False,
        "ordinal": 1,
    },
    {
        "eco": "D02",
        "name": "Queen's Pawn · London",
        "line": "1.d4 d5 2.♘f3 ♘f6 3.♗f4",
        "mastery": 0.42,
        "is_active": False,
        "ordinal": 2,
    },
    {
        "eco": "A04",
        "name": "Réti opening",
        "line": "1.♘f3 d5 2.c4 e6 3.g3",
        "mastery": 0.18,
        "is_active": False,
        "ordinal": 3,
    },
]


@app.get("/repertoire")
@limiter.limit("30/minute")
def get_repertoire(request: Request, player=Depends(get_current_player)):
    """Return the authenticated player's opening repertoire.

    Behaviour:
      - When the player has saved entries, returns those (ordered by
        ordinal ASC, id ASC for stable display).
      - When the player has no saved entries, returns DEFAULT_REPERTOIRE
        — a fresh user sees the canonical 4 lines without the screen
        reading empty.  The defaults are NOT inserted on read to keep
        the GET endpoint side-effect-free; the editing endpoints below
        materialise them on first write via seed_default_repertoire.

    Response shape: {"openings": [...]} where each opening is
    {eco, name, line, mastery, is_active, ordinal}.
    """
    saved = list_repertoire(str(player.id))
    return {"openings": saved if saved else DEFAULT_REPERTOIRE}


# ---------------------------------------------------------------------------
# Repertoire editing — POST add / DELETE remove / POST set-active
# ---------------------------------------------------------------------------
#
# All three endpoints first call seed_default_repertoire() so the
# user can edit the defaults they see in the GET response without an
# explicit "save defaults" step.  Without this, a fresh user who taps
# "set as active" on a default line would silently fail because no
# row exists for that eco yet.


_ECO_RE = re.compile(r"^[A-E][0-9]{2}$|^[A-Z][0-9A-Z]{1,7}$")


def _validate_eco(eco: str) -> str:
    """Permissive ECO validation: standard A00–E99 codes plus
    user-coined identifiers up to 8 chars.  Strips whitespace."""
    eco = eco.strip()
    if not eco:
        raise HTTPException(status_code=400, detail="eco must not be empty")
    if len(eco) > 8:
        raise HTTPException(status_code=400, detail="eco too long (max 8 chars)")
    for ch in eco:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise HTTPException(status_code=400, detail="eco contains control characters")
    if not _ECO_RE.match(eco):
        raise HTTPException(
            status_code=400,
            detail="eco must match standard A00–E99 or be a 1–8 char alnum identifier",
        )
    return eco


def _validate_text_field(value: str, field: str, max_len: int) -> str:
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{field} must not be empty")
    if len(value) > max_len:
        raise HTTPException(status_code=400, detail=f"{field} too long (max {max_len} chars)")
    for ch in value:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise HTTPException(status_code=400, detail=f"{field} contains control characters")
    return value


class RepertoireEntryRequest(BaseModel):
    """Body of POST /repertoire — add or update one opening.

    `mastery` and `is_active` are optional; new entries default to
    mastery=0 and is_active=false.  Use POST /repertoire/{eco}/active
    to change the active line (it enforces the one-active invariant
    across the player's whole list).
    """

    eco: str
    name: str
    line: str
    mastery: float = 0.0

    @field_validator("mastery")
    @classmethod
    def validate_mastery(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("mastery must be in [0.0, 1.0]")
        return float(v)


@app.post("/repertoire")
@limiter.limit("30/minute")
def add_or_update_opening(
    req: RepertoireEntryRequest,
    request: Request,
    player=Depends(get_current_player),
):
    """Add a new opening or update an existing one (upsert by eco).

    The user-facing flow: tap "+" in OpeningsActivity → fill in
    ECO/name/line → POST.  The server seeds defaults first if the
    player has no entries yet, then upserts the new line.

    Returns the full updated repertoire so the client can re-render
    in one round-trip.
    """
    seed_default_repertoire(str(player.id), DEFAULT_REPERTOIRE)
    eco = _validate_eco(req.eco)
    name = _validate_text_field(req.name, "name", 200)
    line = _validate_text_field(req.line, "line", 500)
    upsert_opening(
        player_id=str(player.id),
        eco=eco,
        name=name,
        line=line,
        mastery=req.mastery,
    )
    return {"openings": list_repertoire(str(player.id))}


@app.delete("/repertoire/{eco}")
@limiter.limit("30/minute")
def delete_opening_endpoint(
    eco: str,
    request: Request,
    player=Depends(get_current_player),
):
    """Remove an opening from the player's repertoire.

    Validates the path eco the same way POST /repertoire validates
    its body eco — same character set, same length cap, same
    rejection of control chars.

    Seeds defaults first so a user who deletes one of the canonical
    lines actually drops it from their persistent list (vs. the
    library-of-defaults reappearing on the next GET).

    Returns 404 when no row matches; the client treats that as
    "already gone" and refreshes the list either way.
    """
    eco = _validate_eco(eco)
    seed_default_repertoire(str(player.id), DEFAULT_REPERTOIRE)
    if not delete_opening(str(player.id), eco):
        raise HTTPException(status_code=404, detail="opening not found")
    return {"openings": list_repertoire(str(player.id))}


class DrillResultRequest(BaseModel):
    """Body of POST /repertoire/{eco}/drill-result.

    `outcome` is a self-rated [0, 1] score from the user's drill
    session — the Android client maps "Nailed it" / "Mostly" /
    "Forgot it" to 1.0 / 0.6 / 0.2 respectively.  Future revisions
    may compute it from a real per-move drill engine.
    """

    outcome: float

    @field_validator("outcome")
    @classmethod
    def validate_outcome(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("outcome must be in [0.0, 1.0]")
        return float(v)


# Exponential-moving-average step for mastery updates: a single drill
# nudges mastery toward the outcome by this fraction.  0.2 means
# 5 perfect drills move a fresh line from 0 → ~0.67, and one bad
# drill of a well-mastered line never collapses it below ~80% of the
# previous value — keeps the mastery bar feeling earned, not
# whiplashed.
_MASTERY_EMA_STEP = 0.2


@app.post("/repertoire/{eco}/drill-result")
@limiter.limit("30/minute")
def drill_result_endpoint(
    eco: str,
    req: DrillResultRequest,
    request: Request,
    player=Depends(get_current_player),
):
    """Apply one drill outcome to the named opening's mastery.

    Update math (EMA):
        new = old + step * (outcome - old)
    where step=_MASTERY_EMA_STEP.  Clamped to [0, 1] defensively,
    though both inputs are bounded so the update can't escape the
    range without floating-point noise.

    Seeds defaults first so a user who drills one of the canonical
    lines materialises their personal copy before the mastery bump
    lands.

    Returns the full updated repertoire so the client re-renders
    in one round-trip.
    """
    eco = _validate_eco(eco)
    seed_default_repertoire(str(player.id), DEFAULT_REPERTOIRE)
    saved = list_repertoire(str(player.id))
    target = next((o for o in saved if o["eco"] == eco), None)
    if target is None:
        raise HTTPException(status_code=404, detail="opening not found")

    old = float(target["mastery"])
    proposed = old + _MASTERY_EMA_STEP * (req.outcome - old)
    new_mastery = max(0.0, min(1.0, proposed))

    if not update_opening_mastery(str(player.id), eco, new_mastery):
        # Race: row vanished between our seed/list and the update —
        # treat as 404 so the client can refresh and retry.
        raise HTTPException(status_code=404, detail="opening not found")

    return {"openings": list_repertoire(str(player.id))}


@app.post("/repertoire/{eco}/active")
@limiter.limit("30/minute")
def set_active_opening_endpoint(
    eco: str,
    request: Request,
    player=Depends(get_current_player),
):
    """Mark [eco] as the player's active line.  The two-write
    transaction in repo.set_active_opening enforces the
    "exactly one active" invariant atomically.

    Seeds defaults first so a user who taps "set active" on a
    default line gets it materialised + promoted in one call.

    Returns 404 when the eco doesn't exist for this player after
    seeding (i.e. it's neither a default nor something they've
    added).
    """
    eco = _validate_eco(eco)
    seed_default_repertoire(str(player.id), DEFAULT_REPERTOIRE)
    if not set_active_opening(str(player.id), eco):
        raise HTTPException(status_code=404, detail="opening not found")
    return {"openings": list_repertoire(str(player.id))}


# ------------------------------------------------------------------
# Explain endpoint (LLM layer comes next)
# ------------------------------------------------------------------


@app.post("/explain")
@limiter.limit("30/minute")
def explain(req: AnalyzeRequest, request: Request, player=Depends(get_current_player)):
    engine_signal = extract_engine_signal(req.stockfish_json, fen=req.fen)
    explanation = safe_explainer.explain(engine_signal)

    response = {
        "explanation": explanation,
        "engine_signal": engine_signal,
        "mode": "SAFE_V1",
    }
    validate_explain_response(response)
    return response


@app.post("/explanation_outcome")
@limiter.limit("20/minute")
def report_outcome(req: OutcomeRequest, request: Request, player=Depends(get_current_player)):
    # record_outcome() raises ValueError("Unknown explanation_id") when the id
    # is not already registered via record_explanation().  Nothing in the live
    # request path currently registers ids, so every call landed here would
    # otherwise propagate as 500 with a logged stack trace — a free way for
    # any API-key holder to spam the log pipeline (TRK-01).  Catch it and
    # return a clean 400 with a generic message that does not reveal whether
    # any specific id is or isn't present in the tracker.
    try:
        tracker.record_outcome(**req.model_dump())
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid explanation_id")

    score = tracker.compute_learning_score(req.explanation_id)

    return {"learning_score": score}


# ------------------------------------------------------------------
# Chat endpoint (long-form coaching conversation)
# ------------------------------------------------------------------


@app.post("/chat")
@limiter.limit("10/minute")
async def chat(
    req: ChatRequest,
    request: Request,
    player=Depends(get_current_player),
):
    """Mode-2: long-form coaching explanation for the LLM panel.

    LLM-powered with conversation history, RAG, and Mode-2 validation;
    falls back to deterministic reply when Ollama is unavailable.
    Runs in a thread-pool executor so the async event loop is not blocked.
    """
    turns = [_ChatPipelineTurn(role=t.role, content=t.content) for t in req.messages]
    result = await asyncio.to_thread(
        generate_chat_reply,
        req.fen,
        turns,
        req.player_profile,
        req.past_mistakes,
        req.move_count,
        req.coach_voice,
    )
    response = {
        "reply": result.reply,
        "engine_signal": result.engine_signal,
        "mode": result.mode,
    }
    validate_chat_response(response)
    return response


# ------------------------------------------------------------------
# Streaming chat endpoint (SSE)
# ------------------------------------------------------------------


@app.post("/chat/stream")
@limiter.limit("10/minute")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    player=Depends(get_current_player),
):
    """Streaming variant of POST /chat — same LLM pipeline, chunked via Server-Sent Events.

    Emits one SSE event per word of the coaching reply, then a final ``done``
    event carrying ``engine_signal`` and ``mode``.  Wire format::

        data: {"type": "chunk", "text": "<word> "}\n\n
        ...
        data: {"type": "done", "engine_signal": {...}, "mode": "CHAT_V1"}\n\n

    Uses the same LLM-powered chat_pipeline.generate_chat_reply(); no RL.
    The pipeline runs in a thread-pool executor so the event loop is not blocked.
    """
    turns = [_ChatPipelineTurn(role=t.role, content=t.content) for t in req.messages]
    result = await asyncio.to_thread(
        generate_chat_reply,
        req.fen,
        turns,
        req.player_profile,
        req.past_mistakes,
        req.move_count,
        req.coach_voice,
    )

    # Boundary validation runs before any bytes are streamed so a contract
    # failure surfaces as a clean 500 from FastAPI, not a half-delivered
    # SSE stream the client has to parse to discover the failure.
    validate_chat_response(
        {
            "reply": result.reply,
            "engine_signal": result.engine_signal,
            "mode": result.mode,
        }
    )

    def _generate():
        words = result.reply.split(" ")
        for i, word in enumerate(words):
            text = word if i == len(words) - 1 else word + " "
            yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'engine_signal': result.engine_signal, 'mode': result.mode})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")
