import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import List, Tuple

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
import chess
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

# Shared X-Api-Key verifier — single source of truth for both this debug
# host and the production server.py.  See module docstring for rationale.
from llm.seca.auth.api_key import verify_api_key

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()


# --- Response Class Setup ---
try:
    import orjson  # noqa: F401
    from fastapi.responses import ORJSONResponse

    DefaultResponseClass = ORJSONResponse
except ImportError:
    DefaultResponseClass = JSONResponse

# --- Internal Imports ---
try:
    from .engine_pool import EnginePool
    from .engine_eval import EngineEvaluator
    from .elite_engine_service import EliteEngineService
    from .metrics import miss_metrics_snapshot, record_miss_sample
    from .opening_book import OpeningBook
    from .position_input import normalize_fen
    from .predictive_cache import get_predictions
    from .redis_client import (
        close_redis,
        get_redis_info,
        get_redis_keys,
        get_redis_value,
        redis_client,
        redis_backend_name,
        redis_is_available,
        verify_redis_connection,
    )
except ImportError:
    from engine_pool import EnginePool
    from engine_eval import EngineEvaluator
    from elite_engine_service import EliteEngineService
    from metrics import miss_metrics_snapshot, record_miss_sample
    from opening_book import OpeningBook
    from position_input import normalize_fen
    from predictive_cache import get_predictions
    from redis_client import (
        close_redis,
        get_redis_info,
        get_redis_keys,
        get_redis_value,
        redis_client,
        redis_backend_name,
        redis_is_available,
        verify_redis_connection,
    )

# --- Event Loop Policy ---
if os.name == "nt" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
    current_policy = asyncio.get_event_loop_policy()
    if not isinstance(current_policy, asyncio.WindowsProactorEventLoopPolicy):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

_limiter = Limiter(key_func=get_remote_address)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await verify_redis_connection()
    await engine_pool.start()

    yield

    await engine_pool.stop()
    opening_book.close()
    await close_redis()


app = FastAPI(default_response_class=DefaultResponseClass, lifespan=lifespan)
app.state.limiter = _limiter

_MAX_BODY_BYTES = 512 * 1024
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


class _LimitBodySize(BaseHTTPMiddleware):
    """Request-body size guard.

    Mirrors server.py's middleware including the chunked-encoding bypass
    fix (originally SVD_01 on server.py): a header-only size check lets
    chunked-encoded or otherwise Content-Length-less POST/PUT/PATCH
    bodies through unbounded.  Reject those with HTTP 411 so the limit
    is always enforceable on body-bearing methods.
    """

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is None:
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
# Mirrors server.py — defense-in-depth headers applied to every response.
# host_app.py is the engine-evaluation FastAPI sub-server; if it is ever
# exposed beyond the internal network, browser clients still get a strict
# default-deny posture.  See test_security_headers.py HDR-03.
_CSP_HEADER = (
    "default-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
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


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"error": "Too many requests"})


# Global Exception Handler for Security (CWE-209)
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error") 
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred."}, 
    )


# Initialize Services
engine_pool = EnginePool(size=int(os.getenv("ENGINE_POOL_SIZE", "2")))
engine_eval = EngineEvaluator(engine_pool)
opening_book = OpeningBook()
engine_service = EliteEngineService(engine_eval, opening_book=opening_book)


def _validate_engine_fen(v: str | None) -> str | None:
    if v is None:
        return v
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


class EngineEvalRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    fen: str | None = None
    moves: List[str] = Field(default_factory=list)
    movetime_ms: int | None = Field(
        default=None,
        validation_alias=AliasChoices("movetime_ms", "movetime"),
    )
    nodes: int | None = None

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str | None) -> str | None:
        return _validate_engine_fen(v)

    @field_validator("moves")
    @classmethod
    def validate_moves(cls, v: List[str]) -> List[str]:
        if len(v) > 500:
            raise ValueError("moves too many entries (max 500)")
        for move in v:
            if len(move) > 5:
                raise ValueError("move element too long (max 5 chars)")
        return v

    @field_validator("movetime_ms")
    @classmethod
    def validate_movetime_ms(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 60_000):
            raise ValueError("movetime_ms must be 1–60000")
        return v

    @field_validator("nodes")
    @classmethod
    def validate_nodes(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 10_000_000):
            raise ValueError("nodes must be 1–10000000")
        return v

    @property
    def movetime(self) -> int | None:
        return self.movetime_ms


def _resolve_request_limits(
    *,
    movetime: int | None,
    nodes: int | None,
) -> Tuple[int | None, int | None]:
    return engine_eval.resolve_limits(movetime=movetime, nodes=nodes)


@app.get("/")
def root():
    return {"status": "ok"}




@app.get("/health")
async def health():
    return {
        "status": "ok",
        "engine_pool_available": engine_pool.available,
        "engine_pool_capacity": engine_pool.capacity,
        "opening_book_available": opening_book.available,
        "opening_book_path": opening_book.path if opening_book.available else None,
        "redis_backend": redis_backend_name(),
        "redis_available": await redis_is_available(),
    }


@app.get("/debug/redis")
async def debug_redis(_: None = Depends(verify_api_key)):
    if redis_client is None:
        return {
            "backend": redis_backend_name(),
            "redis": False,
            "detail": "client_unavailable",
        }
    try:
        pong = await redis_client.ping()
        return {
            "backend": redis_backend_name(),
            "redis": bool(pong),
            "pong": pong,
        }
    except Exception:
        logger.exception("Redis connection error during debug check")

        return {
            "backend": redis_backend_name(),
            "redis": False,
            "detail": "Redis connection unavailable",
        }


@app.get("/debug/book")
async def debug_book(_: None = Depends(verify_api_key)):
    return {
        "available": opening_book.available,
        "path": opening_book.path,
    }


@app.post("/engine/eval")
@_limiter.limit("30/minute")
async def eval_position(request: Request, payload: EngineEvalRequest):
    movetime, nodes = _resolve_request_limits(movetime=payload.movetime_ms, nodes=payload.nodes)
    return await _evaluate_position(
        fen=payload.fen,
        moves=payload.moves,
        movetime=movetime,
        nodes=nodes,
    )


@app.get("/engine/eval")
@_limiter.limit("30/minute")
async def eval_position_query(
    request: Request,
    fen: str | None = None,
    moves: List[str] | None = Query(default=None),
    movetime_ms: int | None = None,
    movetime: int | None = None,
    nodes: int | None = None,
):
    req_movetime = movetime_ms if movetime_ms is not None else movetime
    m_time, n_nodes = _resolve_request_limits(movetime=req_movetime, nodes=nodes)
    return await _evaluate_position(
        fen=fen,
        moves=moves or [],
        movetime=m_time,
        nodes=n_nodes,
    )


async def _evaluate_position(
    *,
    fen: str | None,
    moves: List[str] | None,
    movetime: int | None,
    nodes: int | None,
):
    result, metrics = await engine_service.evaluate_with_metrics(
        fen=fen,
        moves=moves,
        movetime=movetime,
        nodes=nodes,
    )
    if not metrics.get("cache_hit", True):
        record_miss_sample(metrics)
    return {
        **result,
        "_metrics": metrics,
    }


@app.get("/debug/engine")
async def debug_engine(_: None = Depends(verify_api_key)):
    capacity = engine_pool.capacity
    available = engine_pool.available
    return {
        "pool_size": capacity,
        "available": available,
        "busy": max(0, capacity - available),
    }


@app.post("/debug/engine-raw")
async def engine_raw(payload: EngineEvalRequest, _: None = Depends(verify_api_key)):
    movetime, nodes = _resolve_request_limits(movetime=payload.movetime_ms, nodes=payload.nodes)
    started = time.perf_counter()
    engine = await engine_pool.acquire()
    wait_ms = round((time.perf_counter() - started) * 1000, 3)

    try:
        eval_started = time.perf_counter()
        result = await engine_eval.evaluate_with_engine(
            engine,
            payload.fen,
            moves=payload.moves,
            movetime=movetime,
            nodes=nodes,
        )
        eval_ms = round((time.perf_counter() - eval_started) * 1000, 3)
    finally:
        await engine_pool.release(engine)

    total_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        **result,
        "_metrics": {
            "engine_wait_ms": wait_ms,
            "engine_eval_ms": eval_ms,
            "total_ms": total_ms,
        },
    }


@app.get("/debug/cache")
async def debug_cache(pattern: str = "cc:*", _: None = Depends(verify_api_key)):
    stats = await get_redis_info("stats")
    return {
        "backend": redis_backend_name(),
        "pattern": pattern,
        "keys": await get_redis_keys(pattern),
        "hits": stats.get("keyspace_hits", 0),
        "misses": stats.get("keyspace_misses", 0),
        "stats": stats,
    }


@app.get("/debug/cache/value")
async def debug_cache_value(key: str, _: None = Depends(verify_api_key)):
    return {
        "backend": redis_backend_name(),
        "key": key,
        "value": await get_redis_value(key),
    }


@app.get("/debug/miss-metrics")
def debug_miss_metrics(_: None = Depends(verify_api_key)):
    return miss_metrics_snapshot()


@app.get("/engine/predictions")
@_limiter.limit("30/minute")
async def engine_predictions(request: Request, fen: str):
    try:
        _validate_engine_fen(fen)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid FEN")
    normalized_fen = normalize_fen(fen) or fen
    return {
        "fen": normalized_fen,
        "predictions": await get_predictions(normalized_fen),
    }


if __name__ == "__main__":
    import uvicorn

    # Use string to avoid import issues in some environments
    U_APP = "host_app:app" if __package__ in (None, "") else "llm.host_app:app"
    uvicorn.run(
        U_APP,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        workers=max(1, int(os.getenv("UVICORN_WORKERS", "4"))),
    )
