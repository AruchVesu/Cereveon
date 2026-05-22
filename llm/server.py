import asyncio
import hmac
import json
import logging
import os
import re
import shutil
import chess
import httpx
import time
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from llm.seca.shared_limiter import limiter
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator

from llm.seca.auth.router import (
    router as auth_router,
    get_current_player,
    get_db,
    init_schema as init_auth_schema,
)
from llm.seca.auth.api_key import verify_api_key
from llm.seca.chat.repo import (
    HISTORY_DEFAULT_LIMIT,
    HISTORY_MAX_LIMIT,
    recent_turns_for_player,
    save_exchange,
)
from llm.seca.events.router import router as game_router
from llm.seca.curriculum.router import router as curriculum_router
from llm.seca.inference.router import router as inference_router
from llm.seca.analytics.router import router as analytics_router
from llm.seca.repertoire.router import router as repertoire_router
from llm.seca.lichess.router import router as lichess_router
from llm.seca.training.router import router as training_router
from llm.seca.mistakes.router import router as mistakes_router
from llm.seca.coach.study_plan.router import router as study_plan_router

# register SECA models
import llm.seca.events.models
import llm.seca.lichess.models  # noqa: F401  # ensure LinkedAccount is on Base before init_schema
import llm.seca.training.models  # noqa: F401  # ensure TrainingCompletion is on Base before init_schema
import llm.seca.coach.study_plan.models  # noqa: F401  # ensure MistakeStudyPlan/Puzzle on Base before init_schema

from llm.seca.engines.stockfish.pool import (
    EnginePoolSettings,
    StockfishEnginePool,
)
from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

# NOTE (PR 10): ``generate_validated_explanation`` was previously
# imported here as a "leave it ready for future wiring" placeholder.
# The function is alive (exercised by ``test_firewall_integration.py``
# and ``test_explain_pipeline_retry.py``) but never wired into any
# HTTP route — the Mode-2 LLM path is reached via /chat and
# /chat/stream; /explain and /seca/explain are intentionally
# deterministic SAFE_V1.  The dead import + its WIRE-06 pinning test
# were both retired to align with reality.
from llm.rag.validators.explain_response_schema import (
    validate_explain_response,
    validate_chat_response,
    validate_live_move_response,
    ExplainSchemaError,
)
from llm.rag.prompts.input_sanitizer import sanitize_user_query
from llm.seca.adaptation.coupling import compute_adaptation
from llm.seca.curriculum.scheduler import CurriculumScheduler
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
    get_active_game,
    get_or_create_auto_game,
)
from llm import observability

logger = logging.getLogger(__name__)
logger.info("Running server from: %s", __file__)
logger.info("SECA safe_mode=%s", SAFE_MODE)

load_dotenv()

API_KEY = os.getenv("SECA_API_KEY")
ENV = os.getenv("SECA_ENV", "dev")
IS_PROD = ENV in {"prod", "production"}
DEBUG = not IS_PROD

# API schema versioning.  Two constants drive the gate:
#
#   API_VERSIONS_SUPPORTED  — every version the server will accept on
#                             inbound coaching requests.  Add a new
#                             version here when shipping a v2 schema;
#                             keep older versions in the tuple for the
#                             grace period during which legacy clients
#                             can still talk to the server.
#   API_VERSION             — the *preferred* (current) version, the
#                             one new clients should target.  Always
#                             equals ``API_VERSIONS_SUPPORTED[-1]``;
#                             retained as a separate name for
#                             backwards-compat with the test pins
#                             (AVH_01) + the doc-constant pin in PR 12.
#
# Bumping the preferred version requires updating ``COACH_API_VERSION``
# in ``android/app/src/main/java/ai/chesscoach/app/ApiVersion.kt`` in
# the same release; bumping the supported range alone (additive) does
# not because old clients keep working.  See README.md > API schema
# versioning for the rollout flow.
#
# Phase 2 gate (PR 14, 2026-05-15): lenient on missing, strict only on
# unsupported version (Phase 1 was strict on any mismatch).  The
# server also advertises ``X-API-Versions-Supported`` on every
# response so clients can discover the accepted range without needing
# a separate /version endpoint.
#
# Pinned by ``llm/tests/test_api_version_header.py`` (AVH_01..AVH_14);
# the README↔code value link is pinned by
# ``llm/tests/test_doc_constants_pinned.py::test_api_version_constant``.
API_VERSIONS_SUPPORTED: tuple[str, ...] = ("1", "2")
API_VERSION = API_VERSIONS_SUPPORTED[-1]

#: Cached for the response middleware so the join doesn't run per
#: request.  Comma-separated to match standard HTTP list semantics
#: (e.g. RFC 7231 Accept, RFC 7231 Cache-Control directives).
_API_VERSIONS_SUPPORTED_HEADER = ", ".join(API_VERSIONS_SUPPORTED)

if IS_PROD and API_KEY is None:
    raise RuntimeError(
        "SECA_API_KEY env var is required in production (SECA_ENV=prod). "
        "Set a non-empty value before starting the server."
    )


# ---- Production-deployment footgun guard ---------------------------------
#
# Closes the residual risk previously documented in
# ``docs/THREAT_MODEL.md`` § T6: a deploy that ships with both
# ``SECA_INSECURE_DEV=true`` AND a production-facing
# ``CORS_ALLOWED_ORIGINS`` (i.e., non-localhost origins) but
# ``SECA_ENV != prod`` would, under the auth flow in
# ``llm.seca.auth.api_key.verify_api_key``, serve every
# X-Api-Key-protected endpoint without authentication.  The previous
# defence was documentation only.
#
# Heuristic for "production-facing": at least one ``CORS_ALLOWED_ORIGINS``
# entry that is not localhost / 127.0.0.1 / [::1] / the Android-emulator
# loopback (10.0.2.2).  Dev contributors who legitimately use the
# insecure flag never set non-loopback CORS origins (the dev defaults
# in ``DEV_CORS_DEFAULTS`` below are all loopback); a deploy that does
# is operating in a production-facing posture and must not also be in
# the insecure-no-auth bypass mode.

_LOOPBACK_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "10.0.2.2",  # Android emulator → host loopback
    }
)


def _looks_like_production_deploy(cors_origins_raw: str) -> bool:
    """Return True when ``cors_origins_raw`` advertises a non-loopback origin.

    Loopback-only origins (the dev defaults) → False.  Empty string or
    whitespace → False.  Any single non-loopback origin → True.

    Uses ``urlsplit().hostname`` for the comparison rather than a
    substring scan — substring matching on ``localhost`` would let
    ``https://localhost.evil.com`` masquerade as loopback, which is
    exactly the bypass a 2026-05-14 reviewer pass caught.  Unparseable
    origins fail closed (treated as production-facing) so a deploy
    cannot evade the gate by submitting malformed CORS strings.

    Pure function on a string so the production guard is independently
    testable; the env-var read happens once at module load below.
    """
    if not cors_origins_raw.strip():
        return False
    from urllib.parse import urlsplit  # noqa: PLC0415

    for raw in cors_origins_raw.split(","):
        origin = raw.strip()
        if not origin:
            continue
        try:
            host = urlsplit(origin).hostname
        except ValueError:
            # Unparseable URL → assume production-facing.  Fails
            # closed so a malformed CORS entry can't slip past.
            return True
        if host is None or host.lower() not in _LOOPBACK_HOSTS:
            return True
    return False


_INSECURE_DEV = os.getenv("SECA_INSECURE_DEV", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
if (
    _INSECURE_DEV
    and not IS_PROD
    and _looks_like_production_deploy(os.getenv("CORS_ALLOWED_ORIGINS", ""))
):
    raise RuntimeError(
        "Production-deployment footgun: SECA_INSECURE_DEV is set AND "
        "CORS_ALLOWED_ORIGINS contains a non-loopback origin, but "
        "SECA_ENV is not 'prod'.  This combination would serve every "
        "X-Api-Key-protected endpoint without authentication on what "
        "appears to be a production-facing deploy.  Resolution: remove "
        "SECA_INSECURE_DEV from the environment, set SECA_ENV=prod (and "
        "configure SECA_API_KEY), or restrict CORS_ALLOWED_ORIGINS to "
        "loopback-only origins.  See docs/THREAT_MODEL.md § T6."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine_pool, scheduler, world_model

    # Install JSON log formatter when SECA_ENV=prod or COACH_LOG_JSON=1.
    # Must run AFTER uvicorn has set up its loggers (which uvicorn does
    # during lifespan startup, not at module import), so a uvicorn
    # reload doesn't clobber the formatter.  No-op in dev unless the
    # explicit override is set.
    from llm import log_config as _log_config  # noqa: PLC0415

    if _log_config.configure_logging():
        logger.info("JSON structured logging enabled")

    try:
        init_db()
        # SQLAlchemy schema + small SQLite-only migrations.  Moved out of
        # auth/router.py module-import time so importing the router (e.g.
        # to access Pydantic request models in tests) no longer pays the
        # cost of opening the DB and running DDL.
        init_auth_schema()

        # Sweep Lichess import jobs left in ``queued`` / ``running`` by
        # a prior crash or SIGTERM.  Their worker thread is gone; the
        # row would otherwise block ``start_import_job`` coalescing
        # forever.  Cheap — runs once, idempotent.  Must run AFTER
        # init_auth_schema so the table exists.
        from llm.seca.lichess.import_service import (  # noqa: PLC0415
            cleanup_stale_import_jobs_on_startup,
        )

        cleanup_stale_import_jobs_on_startup()

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
        # Expose on app.state for routes that need it without late
        # imports.  The events router uses this for the server-side
        # PGN accuracy recompute on /game/finish (closes the
        # client-trust gap documented in docs/SECA.md "Trust
        # property of the reward signal").
        app.state.engine_pool = engine_pool
        # Mistake-replay verifier injection.  The
        # /training/verify-replay handler in
        # ``llm.seca.mistakes.router`` reads from a module-global so
        # it doesn't have to import ``llm.server`` (that would create
        # a circular import — server.py registers the mistakes
        # router).  Pattern matches ``observability.register_*`` —
        # one-shot wiring at lifespan startup, mirrored at shutdown.
        from llm.seca.mistakes.router import (  # noqa: PLC0415
            set_engine_pool as _set_mistakes_pool,
        )
        _set_mistakes_pool(engine_pool)
        scheduler = CurriculumScheduler()

        # Register the engine pool snapshot provider for Prometheus
        # /metrics gauges.  Callback-based so the acquire/release hot
        # path stays free of Prom overhead; the snapshot is taken at
        # scrape time only.  Captures pool_size + qsize at call time
        # so the provider survives a pool restart in dev.
        def _engine_pool_snapshot() -> dict[str, int]:
            if engine_pool is None:
                return {"size": 0, "available": 0, "in_use": 0}
            size = engine_pool.settings.pool_size
            available = engine_pool.qsize()
            return {
                "size": size,
                "available": available,
                "in_use": max(0, size - available),
            }

        observability.register_engine_pool_provider(_engine_pool_snapshot)

        logger.info("DB initialized")
        logger.info("Stockfish engine pool initialized (size=%d)", settings.pool_size)
    except SystemExit:
        # The SECA safety freeze (``llm.seca.safety.freeze._crash``)
        # raises ``SystemExit(1)`` via ``sys.exit`` when the runtime
        # is unsafe (forbidden brain module pre-loaded, SAFE_MODE off
        # in prod, ...).  ``SystemExit`` inherits from
        # ``BaseException``, not ``Exception``, so the broad
        # ``except Exception`` below does not catch it by construction
        # — pinned by ``test_safety_freeze.py::
        # test_lifespan_crashes_with_forbidden_brain_module``.  This
        # explicit re-raise is defensive: it documents the
        # "freeze-must-propagate" invariant so a future contributor
        # cannot accidentally widen the catch (e.g. to
        # ``except BaseException``) and silently downgrade the safety
        # refusal to an "engine pool disabled" warning that lets the
        # process keep serving.  The "memory" note on lifespan testing
        # (anyio TaskGroup wrapping under TestClient) documents the
        # one path where this re-raise is not enough on its own — that
        # path is exclusive to tests; production uvicorn propagates
        # ``SystemExit`` unwrapped.
        raise
    except Exception as e:
        if engine_pool:
            engine_pool.close()
        engine_pool = None
        # Keep app.state in sync with the global so any route that
        # late-binds via getattr(request.app.state, "engine_pool", None)
        # falls back cleanly when startup failed mid-way.
        app.state.engine_pool = None
        # Clear the mistakes verifier's pool reference too — without
        # this, a failed startup would leave the verifier holding a
        # half-initialised pool from a previous run.
        try:
            from llm.seca.mistakes.router import (  # noqa: PLC0415
                set_engine_pool as _set_mistakes_pool,
            )
            _set_mistakes_pool(None)
        except Exception:  # noqa: BLE001 — never mask the original error
            pass
        logger.error("Stockfish engine pool DISABLED: %s", e)

    yield

    # Shut down the Lichess import worker pool.  ``wait=False`` +
    # ``cancel_futures=True`` cancels any queued (not-yet-started)
    # tasks immediately and lets running workers race uvicorn's
    # graceful-shutdown timeout (default 30s).  We do NOT block on
    # in-flight Lichess streams because ``httpx.iter_lines`` can hold
    # the thread up to its 60s read timeout per chunk — that's a
    # multi-minute tail at process exit.  Any row left ``running`` is
    # swept by ``cleanup_stale_import_jobs_on_startup`` on next boot.
    try:
        from llm.seca.lichess.router import _executor as _lichess_import_executor

        _lichess_import_executor.shutdown(wait=False, cancel_futures=True)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Lichess import executor shutdown error: %s", exc)

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
    # ``X-API-Version`` is explicitly allowed so browser / WebView clients
    # can send the schema-version header through a CORS preflight without
    # tripping the default ``Access-Control-Allow-Headers`` filter.  See
    # the api_version_gate middleware below for the enforcement semantics.
    allow_headers=["Authorization", "Content-Type", "X-Api-Key", "X-API-Version"],
    # Browser scripts can only read a small CORS-safelisted set of
    # response headers without an explicit expose_headers list.  The
    # two API-versioning headers are exposed here so browser-based
    # clients (dev tools, future web UI) can read the server's
    # accepted-version range without parsing the body.  Non-browser
    # clients (Android via OkHttp/HttpURLConnection) read all response
    # headers regardless and are unaffected.
    expose_headers=["X-API-Version", "X-API-Versions-Supported"],
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


# ---- API schema version gate ---------------------------------------------
# Stamps ``X-API-Version`` on every response and (Phase 1) gates inbound
# requests on the matching header:
#   * Missing  → proceed; INFO log so the operator can watch the rollout
#                migrate to fully-versioned clients.
#   * Match    → proceed silently.
#   * Mismatch → 400 with a JSON ``detail`` naming both versions.
# Discovery routes (``/``, ``/health``, ``/seca/status``) never reject so
# an out-of-date client can still read the server version off the same
# open endpoint it polls for the SECA safety gate.  Pinned by
# llm/tests/test_api_version_header.py (AVH_01..AVH_10).
_DISCOVERY_PATHS = frozenset({"/", "/health", "/seca/status"})


@app.middleware("http")
async def api_version_gate(request: Request, call_next):
    client_version = request.headers.get("x-api-version")
    is_discovery = request.url.path in _DISCOVERY_PATHS

    if (
        not is_discovery
        and client_version is not None
        and client_version not in API_VERSIONS_SUPPORTED
    ):
        return JSONResponse(
            status_code=400,
            content={
                "detail": (
                    f"X-API-Version mismatch: client sent {client_version!r}, "
                    f"server supports [{_API_VERSIONS_SUPPORTED_HEADER}] "
                    f"(current: {API_VERSION!r}).  Update the client to a "
                    f"supported version."
                )
            },
            headers={
                "X-API-Version": API_VERSION,
                "X-API-Versions-Supported": _API_VERSIONS_SUPPORTED_HEADER,
            },
        )

    if not is_discovery and client_version is None:
        # Lenient — log once per request so operators can see the
        # migration rate of clients sending the version header without
        # parsing access logs.
        logger.info(
            "X-API-Version header missing on %s; proceeding (lenient mode)",
            request.url.path,
        )

    response = await call_next(request)
    response.headers["X-API-Version"] = API_VERSION
    response.headers["X-API-Versions-Supported"] = _API_VERSIONS_SUPPORTED_HEADER
    return response


# ---- request_id + structured request-end log ----------------------------
# request_id is generated per request (or read from a client-supplied
# X-Request-ID), bound to a contextvar so any logger.info() emitted by
# downstream handlers picks it up, and echoed back via the response
# header so the client / load balancer can correlate.  The same id is
# included in the request-end INFO line below, which is the structured
# log row a Loki/Datadog dashboard would group by.
import uuid as _uuid  # noqa: E402

from llm import log_config  # noqa: E402

_X_REQUEST_ID = "X-Request-ID"


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    # Trust a client-supplied X-Request-ID only as a hint — never use
    # it as a security identifier — and gate it through the
    # is_valid_client_request_id helper (length cap + ASCII).  Fresh
    # UUID otherwise so log correlation still works.
    raw = request.headers.get(_X_REQUEST_ID, "").strip()
    if log_config.is_valid_client_request_id(raw):
        request_id = raw
    else:
        request_id = _uuid.uuid4().hex

    token = log_config.set_request_id(request_id)
    try:
        response = await call_next(request)
    finally:
        log_config.request_id_var.reset(token)

    response.headers[_X_REQUEST_ID] = request_id
    return response


# ---- Prometheus HTTP metrics ---------------------------------------------
# Sits OUTERMOST in the middleware stack so the timer wraps every inner
# middleware (body-size limit, security headers, method override, version
# gate) — what we want recorded is the wall-clock time the client sees.
# The route template is read from ``request.scope`` AFTER call_next so
# the cardinality of the ``path_template`` label is bounded by the
# number of registered routes (FastAPI sets ``scope["route"]`` during
# routing, before this middleware regains control).  Self-counting is
# skipped for ``/metrics`` so a Prometheus scrape doesn't show up in the
# histogram it just produced.
_METRICS_PATH = "/metrics"


@app.middleware("http")
async def prometheus_http_middleware(request: Request, call_next):
    if request.url.path == _METRICS_PATH:
        return await call_next(request)

    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    route = request.scope.get("route")
    path_template = getattr(route, "path", None) or "unmatched"
    status = str(response.status_code)
    method = request.method

    observability.http_requests_total.labels(
        method=method, path_template=path_template, status=status
    ).inc()
    observability.http_request_duration_seconds.labels(
        method=method, path_template=path_template, status=status
    ).observe(duration)

    # Structured request-end log line.  Picked up by the JSON formatter
    # in prod (SECA_ENV=prod) and rendered as the readable default in
    # dev — same call site either way.  ``extra={...}`` fields are
    # copied verbatim into the JSON payload by JsonLogFormatter.
    logger.info(
        "request completed",
        extra={
            "method": method,
            "path": request.url.path,
            "path_template": path_template,
            "status": response.status_code,
            "latency_ms": round(duration * 1000, 3),
            "client_ip": request.client.host if request.client else None,
        },
    )

    return response


@app.middleware("http")
async def commit_pending_auth_rotation(request: Request, call_next):
    """Commit JWT rotation queued by ``get_current_player`` only on 2xx.

    ``get_current_player`` mints a fresh JWT for every authenticated call
    and stashes ``(session_id, new_token)`` on
    ``request.state.pending_auth_rotation``.  This middleware completes
    the rotation — DB write + ``X-Auth-Token`` response header — but ONLY
    when the route handler produced a 2xx response.

    Why this split exists (issue #130)
    ----------------------------------
    The pre-#130 design committed rotation inside ``get_current_player``
    itself, BEFORE the route handler ran.  If the handler then failed
    (e.g. a Mode-1 hint that 500s the boundary validator, which was
    issue #129), the JWT had already been revoked server-side, but the
    new token never reached the client (FastAPI's exception handler
    drops dependency-set response headers).  Result: the client was
    locked out of every authenticated route until re-login.

    Why the DB session is opened here rather than threaded through
    --------------------------------------------------------------
    The per-request DB session from ``Depends(get_db)`` is already
    closed by the time middleware runs, so we open a short-lived
    session via ``SessionLocal()``.  One extra round-trip per
    authenticated request is acceptable; correctness wins.

    Why nothing happens on 5xx / 4xx
    --------------------------------
    The pending rotation is silently discarded.  The previously-
    presented JWT remains the one bound to the session (rotation was
    never committed), so the client's next call with that same token
    succeeds.  This is exactly the property the cascade lockout
    violated.
    """
    response = await call_next(request)
    pending = getattr(request.state, "pending_auth_rotation", None)
    if pending is None:
        return response
    if not (200 <= response.status_code < 300):
        # Non-success — discard the rotation.  Old token stays valid;
        # the next call from the client succeeds.
        return response

    # Defer the SessionLocal import to call-time so this middleware is
    # safe to import before lifespan completes (init_schema not yet run).
    from llm.seca.auth.router import SessionLocal
    from llm.seca.auth.service import AuthService

    try:
        with SessionLocal() as db:
            AuthService(db).rotate_session_token(pending["session_id"], pending["new_token"])
    except Exception as exc:  # noqa: BLE001
        # If the rotation write itself fails (DB hiccup), prefer to NOT
        # set X-Auth-Token — the server-side token hash is unchanged so
        # the old JWT remains valid.  Log so we notice persistent issues
        # without locking users out.
        logger.warning(
            "auth token rotation commit failed (%s: %s) — leaving old token in force",
            type(exc).__name__,
            exc,
        )
        return response

    response.headers["X-Auth-Token"] = pending["new_token"]
    return response


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"error": "Too many requests"},
        headers={
            "X-API-Version": API_VERSION,
            "X-API-Versions-Supported": _API_VERSIONS_SUPPORTED_HEADER,
        },
    )


app.include_router(auth_router)
app.include_router(game_router)
app.include_router(curriculum_router)
app.include_router(analytics_router)
app.include_router(repertoire_router)
app.include_router(lichess_router)
app.include_router(training_router)
app.include_router(mistakes_router)
app.include_router(study_plan_router)
app.include_router(
    inference_router,
    prefix="/seca",
    tags=["seca-inference"],
    dependencies=[Depends(verify_api_key)],
)
scheduler: CurriculumScheduler | None = None
world_model: SafeWorldModel | None = None
safe_explainer = SafeExplainer()

# ------------------------------------------------------------------
# Engine lifecycle
# ------------------------------------------------------------------

engine_pool: StockfishEnginePool | None = None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


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


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------


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


class AnalyzeRequest(BaseModel):
    """Request shape for ``/analyze`` and ``/explain``.

    Pre-PR-9 this carried an optional ``stockfish_json: dict`` field
    that the route handlers piped directly into ``extract_engine_signal``.
    That was a trust-boundary inconsistency with the architecture
    invariant *"Stockfish JSON: Trusted"* (which implies the JSON is
    server-authentic, not client-supplied).  A modded client could
    claim any position evaluation and the server would build an ESV
    from it.  Practical impact was bounded (``/explain`` is
    SafeExplainer-only, no LLM gating; ``/analyze`` just returns the
    ESV to the caller — both deceiving only the client itself), but
    the architectural inconsistency was real.

    PR 9 removes the field.  Pydantic's default extra-field policy
    (``ignore``) silently drops any ``stockfish_json`` a back-compat
    client still sends, so this is not a breaking change for the
    sending side; only the server's access to that value is gone.
    Both handlers now build the ESV from FEN-only heuristics via
    ``extract_engine_signal(None, fen=req.fen)`` — the same fall-back
    path that already handled missing/empty stockfish_json.

    Routes that need real engine evaluation (``/live/move``,
    ``/seca/explain``) acquire the engine pool server-side and feed
    extract_engine_signal with the authentic Stockfish output — that
    path is unchanged.
    """

    fen: str
    user_query: str | None = ""

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        return _validate_fen_field(v)

    @field_validator("user_query")
    @classmethod
    def validate_user_query(cls, v: str | None) -> str | None:
        if v and len(v) > 2000:
            raise ValueError("user_query too long (max 2000 chars)")
        return sanitize_user_query(v) if v else v


class EngineEvalRequest(BaseModel):
    """Body of POST /engine/eval — Android's HttpEngineEvalClient sends
    only a FEN.  Previously hosted by the standalone host_app.py debug
    server (never deployed to production, so the Android calls 404'd
    silently and the eval-after-AI-move badge in MainActivity rendered
    "⚠ Eval N/A" until this route was migrated to server.py).

    Contract intentionally narrower than host_app's: drops the unused
    GET variant + ``moves``/``movetime_ms``/``nodes`` fields that no
    in-tree caller sends.  Adding them back is a contract widening
    that requires an Android client update in the same release.
    """

    fen: str

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        return _validate_fen_field(v)


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
    """Build the ESV from ``req.fen`` using FEN-only heuristics.

    Single decision point for ``/analyze`` and ``/explain``: both
    routes feed FEN to ``extract_engine_signal`` and accept the ESV
    the FEN-derived enrichment path produces.  No client-supplied
    Stockfish JSON reaches this helper — see ``AnalyzeRequest``
    docstring for the PR 9 trust-boundary fix rationale.

    Real engine-eval consumers (``/live/move``, ``/seca/explain``)
    acquire the engine pool and feed ``extract_engine_signal`` with
    authentic Stockfish JSON; this helper is NOT on that path.
    """
    return extract_engine_signal(None, fen=req.fen)


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


def verify_metrics_auth(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Authenticate /metrics scrapes against ``SECA_API_KEY``.

    Accepts both header shapes so a Prometheus server can scrape with
    its native ``bearer_token`` config OR with the codebase's
    ``X-Api-Key`` convention used by every other protected route:
      * ``Authorization: Bearer <SECA_API_KEY>``
      * ``X-Api-Key: <SECA_API_KEY>``

    Constant-time comparison via ``hmac.compare_digest`` matches the
    pattern in ``llm.seca.auth.api_key``.
    """
    if API_KEY is None:
        # Same dev-vs-prod stance as verify_api_key: prod must be
        # explicitly configured, dev without an API_KEY allows scrapes
        # so operators iterating locally don't need to plumb a key.
        if IS_PROD:
            raise HTTPException(status_code=500, detail="Server misconfiguration")
        return

    if x_api_key and hmac.compare_digest(x_api_key, API_KEY):
        return

    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token and hmac.compare_digest(token, API_KEY):
            return

    raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/metrics")
def metrics_endpoint(_: None = Depends(verify_metrics_auth)) -> Response:
    """Prometheus exposition endpoint.

    Authenticated against ``SECA_API_KEY`` via X-Api-Key or Bearer
    Authorization (see ``verify_metrics_auth``).  Skipped by the HTTP
    request middleware so /metrics scrapes don't appear in the very
    histogram they just produced.

    Content-Type is the Prometheus text exposition format (with
    version suffix), as emitted by ``prometheus_client.CONTENT_TYPE_LATEST``.
    """
    return Response(
        content=observability.get_metrics_text(),
        media_type=observability.METRICS_CONTENT_TYPE,
    )


@app.get("/seca/status")
def seca_status():
    """Return the SECA runtime safety flag.

    Open endpoint (no auth): readable by Android at cold-start so the
    client can confirm ``safe_mode`` is active before sending coaching
    requests.

    Implementation: calls ``verify_runtime_safety(world_model)`` (the
    per-request twin of the lifespan-startup ``enforce``) so the
    returned boolean reflects the **current** runtime — not just the
    boot-time ``SAFE_MODE`` constant.  A future lazy import of any
    forbidden ``brain.*`` module after startup would flip this to
    False on the next request, surfacing drift to Android clients
    without crashing the process.  Pre-PR-6 the endpoint returned
    the module-level constant only (flagged as dead-code-by-docstring
    by the PR 1 reviewer pass; see ``docs/SECA.md`` "Freeze guard").

    Response is intentionally minimal — the previous shape exposed
    ``bandit_enabled`` (redundant; just ``not safe_mode``) and
    ``version`` ("1.0", unused by any client decision), both of which
    were small information-disclosure surfaces with no compensating
    use case.
    """
    try:
        # Lazy import keeps test stubs (which do not import server.py)
        # from paying the cost of resolving the safety package at the
        # /seca/status fixture boundary.
        from llm.seca.safety.freeze import verify_runtime_safety  # noqa: PLC0415

        ok, _reason = verify_runtime_safety(world_model)
    except Exception:  # noqa: BLE001
        # Defensive: a per-request scan failure here must not crash the
        # endpoint.  Startup ``enforce`` already validated the runtime
        # once; fall back to the module-level constant so the
        # endpoint always returns a usable shape.  The exception is
        # logged for operator visibility.
        logger.exception(
            "verify_runtime_safety raised in /seca/status; " "falling back to SAFE_MODE constant"
        )
        ok = SAFE_MODE
    return {"safe_mode": ok}


@app.get("/llm/health")
@limiter.limit("10/minute")
async def llm_health(request: Request):
    """Single-shot probe of the configured DeepSeek backend.

    Open endpoint (no auth): the deterministic-fallback path inside
    ``chat_pipeline`` and ``live_move_pipeline`` swallows LLM
    failures so users still get *some* coach reply.  That keeps
    coaching available during transient hiccups but also hides
    persistent outages (e.g. invalid API key, billing issue, DeepSeek
    downtime) behind 200-OK responses to ``/chat``.  This endpoint
    surfaces the signal directly so uptime monitors and operators
    can distinguish a healthy edge ("/health 200") from a healthy
    *coaching pipeline*.

    Probe: a single ``POST {DEEPSEEK_URL}`` with ``max_tokens: 1``
    against the configured model.  This is the cheapest way to
    confirm three things at once — API key valid, model name
    accepted, network path open.  No "tags" stage like the prior
    Ollama-flavoured endpoint, because DeepSeek has no concept of
    locally-pulled models.

    Response shape::

        {
          "ok": bool,
          "provider": "deepseek",
          "model": str,            # MODEL_NAME from env
          "api_base": str,         # DEEPSEEK_API_BASE
          "latency_ms": float,     # total wall time
          "error": str,            # only when ok=false
        }

    Status code is always 200 even on failure — this is a probe, not
    a request that itself failed.  Operators / monitors should key
    on the ``ok`` field, not HTTP status.

    Rate-limited to 10/minute to keep an attacker from using this
    endpoint to fingerprint the LLM provider or burn through your
    DeepSeek balance via probe-floods.  Generous enough that uptime
    tools can poll it safely (every 30-60s typical).

    Bounded timeout (default 10s, override via
    ``LLM_HEALTH_GENERATE_TIMEOUT_S``) so a hung DeepSeek call
    doesn't stall the probe — a probe that itself times out IS a
    useful signal.
    """
    from llm.seca.coach.explain_pipeline import DEEPSEEK_API_BASE, DEEPSEEK_URL, MODEL_NAME

    started = time.perf_counter()
    timeout_s = float(os.getenv("LLM_HEALTH_GENERATE_TIMEOUT_S", "10"))

    def _ms() -> float:
        return round((time.perf_counter() - started) * 1000.0, 2)

    api_key = os.getenv("COACH_DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return {
            "ok": False,
            "provider": "deepseek",
            "model": MODEL_NAME,
            "api_base": DEEPSEEK_API_BASE,
            "latency_ms": _ms(),
            "error": "COACH_DEEPSEEK_API_KEY is unset",
        }

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                DEEPSEEK_URL,
                json={
                    "model": MODEL_NAME,
                    "messages": [{"role": "user", "content": "ok"}],
                    "stream": False,
                    "max_tokens": 1,
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code != 200:
            # Surface the upstream error body when present — DeepSeek
            # returns a JSON ``{"error":{"message":...}}`` on 4xx that
            # is much more useful than a bare status code.
            try:
                upstream = resp.json().get("error", {}).get("message") or resp.text[:200]
            except Exception:  # noqa: BLE001
                upstream = resp.text[:200]
            return {
                "ok": False,
                "provider": "deepseek",
                "model": MODEL_NAME,
                "api_base": DEEPSEEK_API_BASE,
                "latency_ms": _ms(),
                "error": f"HTTP {resp.status_code}: {upstream}",
            }
        body = resp.json()
        # Confirm the response carries the choices[0].message.content
        # path that ``call_llm`` reads from — catches a "200 with
        # unexpected JSON shape" provider regression.
        try:
            _ = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return {
                "ok": False,
                "provider": "deepseek",
                "model": MODEL_NAME,
                "api_base": DEEPSEEK_API_BASE,
                "latency_ms": _ms(),
                "error": "200 with unexpected response shape (no choices[0].message.content)",
            }
    except Exception as exc:  # noqa: BLE001
        # CWE-209: exception messages can carry sensitive substrings —
        # e.g. an httpx error formatted with the full request URL,
        # which on auth failure includes the upstream's echoed API
        # key fragment.  The exception class name alone is enough for
        # operators to triage (``httpx.ConnectError`` vs
        # ``httpx.TimeoutException`` vs ``httpx.HTTPStatusError``)
        # without leaking payload.  Log the full ``exc`` server-side
        # so on-call still has the detail when needed.
        logger.warning("llm_health probe failed: %s", exc, exc_info=True)
        return {
            "ok": False,
            "provider": "deepseek",
            "model": MODEL_NAME,
            "api_base": DEEPSEEK_API_BASE,
            "latency_ms": _ms(),
            "error": f"{type(exc).__name__}: probe failed",
        }

    return {
        "ok": True,
        "provider": "deepseek",
        "model": MODEL_NAME,
        "api_base": DEEPSEEK_API_BASE,
        "latency_ms": _ms(),
    }


@app.get("/debug/engine")
def engine_debug(_: None = Depends(verify_api_key)):
    if engine_pool is None:
        return {"pool_size": 0}
    return {"pool_size": engine_pool.qsize()}


# ------------------------------------------------------------------
# /move + /adaptation/mode + dynamic-adaptation cluster RETIRED in
# PR 23 (2026-05-15) after the SECA-Android wiring audit confirmed
# no Android caller ever emerged.  The four surfaces (POST /move,
# POST /adaptation/mode, GET /adaptation/mode, and the in-process
# ``_dynamic_registry`` / DynamicModeRegistry / dynamic_mode.py
# module) were a connected feature for first-play skill-assessment
# sessions that never landed in the client.  /live/move's
# previous ``_dynamic_registry.record_move_quality`` /
# ``dynamic_adaptation`` integration is also gone below.
# ------------------------------------------------------------------


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
    the DeepSeek call fails.  Runs in a thread-pool executor so the
    async event loop is not blocked during Stockfish + DeepSeek work.

    Stockfish evaluation
    --------------------
    Before invoking the Mode-1 LLM pipeline we run Stockfish against
    the post-move FEN to populate ``stockfish_json``.  Without this,
    ``extract_engine_signal`` falls back to a FEN-only heuristic that
    can't see hanging pieces or tactical threats — the LLM then writes
    "solid, balanced" replies regardless of whether the human just
    walked into a fork.  See PR #87 and probe transcript dated
    2026-05-10 for the symptom that prompted this wiring.

    On any engine-pool failure (no pool, queue exhausted, engine
    crashed) we skip the eval and pass ``stockfish_json=None``, which
    preserves the pre-PR-#87 heuristic-only behaviour as the graceful
    degradation path — coaching never errors, just loses tactical
    nuance for that one move.
    """
    adaptation = compute_adaptation(player.rating, player.confidence)

    stockfish_json: dict | None = None
    if engine_pool is not None:
        try:
            stockfish_json = await asyncio.to_thread(
                engine_pool.evaluate_position,
                fen=req.fen,
                movetime_ms=200,
            )
        except Exception as exc:  # noqa: BLE001
            # Engine unavailable or crashed; fall back to heuristic-only
            # engine signal.  Log at WARNING because this affects every
            # live-move hint until the pool recovers.
            logger.warning(
                "Stockfish eval failed for /live/move (%s: %s); using heuristic",
                type(exc).__name__,
                exc,
            )

    result = await asyncio.to_thread(
        generate_live_reply,
        req.fen,
        req.uci,
        str(player.id),
        adaptation["teaching"]["style"],
        stockfish_json,
    )
    response = {
        "status": "ok",
        "hint": result.hint,
        "engine_signal": result.engine_signal,
        "move_quality": result.move_quality,
        "mode": result.mode,
    }
    try:
        validate_live_move_response(response)
    except ExplainSchemaError as exc:
        # Defense-in-depth — the pipeline already runs every Mode-2 gate
        # the boundary re-runs, so an ExplainSchemaError here means a
        # validator drift the pipeline didn't catch (newly-added
        # forbidden token, schema change).  Returning 500 would burn the
        # rotated JWT (issue #130) and lock out the session.  Swap the
        # offending hint for the deterministic fallback — it is
        # constructed to satisfy every gate by construction — and log
        # the original payload at WARNING so the drift is fixable
        # without losing a user session.
        logger.warning(
            "validate_live_move_response rejected pipeline hint (%s); "
            "substituting deterministic fallback",
            exc,
        )
        fallback = await asyncio.to_thread(
            generate_live_reply,
            req.fen,
            req.uci,
            str(player.id),
            adaptation["teaching"]["style"],
            stockfish_json,
            True,  # force_deterministic — skip LLM, emit hand-tuned fallback
        )
        response["hint"] = fallback.hint
        response["move_quality"] = fallback.move_quality
        response["mode"] = fallback.mode
        response["engine_signal"] = fallback.engine_signal
        # Re-validate; if the deterministic fallback itself fails the
        # boundary, that is a structural bug we DO want to surface.
        validate_live_move_response(response)
    return response


# ``/engine/eval`` — Android's per-move Stockfish score + best-move
# endpoint.  Returns ``{"score": <centipawns>, "best_move": <uci>,
# "source": "engine"}``, matching ``HttpEngineEvalClient.parseResponse``.
#
# Migrated from the standalone ``host_app.py`` debug server in the
# host_app retirement pass.  ``host_app:app`` was wired up by an old
# ``llm/Dockerfile`` (orphaned for months — the production
# ``llm/Dockerfile.api`` runs ``llm.server:app``), so this endpoint
# 404'd in production for an unknown stretch and the Android
# MainActivity rendered "⚠ Eval N/A" on the per-AI-move eval badge.
#
# Contract is narrower than host_app's:
#   - POST only (no GET variant; nothing in-tree sends GET).
#   - Body is just ``{"fen": str}``; ``moves`` / ``movetime_ms`` /
#     ``nodes`` were never used by Android's HttpEngineEvalClient.
#   - X-Api-Key gated (host_app's variant was unauthenticated; tightened
#     here because Android already sends the key via BuildConfig and
#     the heavy compute endpoint shouldn't be a public DoS surface).
@app.post("/engine/eval")
@limiter.limit("30/minute")
def engine_eval(
    req: EngineEvalRequest,
    request: Request,
    _: None = Depends(verify_api_key),
):
    if engine_pool is None:
        # Engine pool failed to boot — return a degraded shape rather
        # than 500 so the Android client's ``engineAvailable=false``
        # fallback path fires cleanly instead of treating it as a
        # network error.
        return {"score": None, "best_move": None, "source": "unavailable"}

    fen = req.fen
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid FEN") from exc

    # 200 ms matches the per-move budget used by /live/move (server.py
    # line 1462 in the existing live_move handler).  Long enough for
    # Stockfish to find depth-12-ish moves at default skill; short
    # enough that the 30/minute rate limit doesn't compound into a
    # noticeable per-request wait when the pool is busy.
    movetime_ms = 200

    # Acquire an engine directly and run analyse — engine_pool's
    # evaluate_position() only returns the score (used by /live/move
    # for engine_signal); we need both score + best_move.  Bypassing
    # evaluate_position keeps both fields in one analyse() round-trip.
    engine = None
    try:
        engine = engine_pool._engines.get(  # noqa: SLF001
            timeout=max(0.001, engine_pool.settings.queue_timeout_ms / 1000.0)
        )
    except Exception as exc:  # queue.Empty or pool not started
        logger.warning("engine_pool acquire failed for /engine/eval: %s", exc)
        return {"score": None, "best_move": None, "source": "unavailable"}

    try:
        info = engine.analyse(board, chess.engine.Limit(time=movetime_ms / 1000.0))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stockfish analyse failed for /engine/eval: %s", exc)
        return {"score": None, "best_move": None, "source": "unavailable"}
    finally:
        engine_pool._release_engine(engine)  # noqa: SLF001

    score_obj = info.get("score") if isinstance(info, dict) else None
    pv = info.get("pv") if isinstance(info, dict) else None

    score_cp: int | None = None
    if score_obj is not None:
        white_score = score_obj.white()
        if white_score.is_mate():
            mate_in = white_score.mate() or 0
            # Convention matches the rest of the codebase: ±10000 for
            # mate, signed by side (positive = White mates).
            score_cp = 10000 if mate_in > 0 else -10000
        else:
            score_cp = int(white_score.score(mate_score=10000) or 0)

    best_move: str | None = None
    if pv:
        try:
            best_move = pv[0].uci()
        except (IndexError, AttributeError):
            best_move = None

    return {"score": score_cp, "best_move": best_move, "source": "engine"}


# /next-training/{player_id} RETIRED in PR 26 (2026-05-15).  Was a
# placeholder implementation with hardcoded "demo weaknesses" that
# never advanced past the comment in the source.  Android always
# called POST /curriculum/next first (the SECA-driven authoritative
# path); /next-training was the fallback that ran when
# /curriculum/next failed — but the fallback was showing fake-data
# recommendations, not a real signal.  Removed alongside the
# Android-side ``getNextTraining`` method + ``TrainingRecommendation``
# DTO + ``GameSummaryBottomSheetTrainingTest`` + the schema-conflict
# pin in test_api_contract_validation.py.


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

    fen: full FEN of the current position.  Validated through the
        canonical ``_validate_fen_field`` shared with /move, /live/move,
        /analyze, /explain, /chat — 100-char cap, six FEN fields,
        verified parseable by ``chess.Board()``.  Pre-Sprint-5.B
        validation only rejected control chars + capped at 256, so a
        256-char malformed FEN was accepted, stored, and later served
        back to clients via /game/active (audit finding F-10).  The
        unified validator closes that path while staying compatible
        with every legitimate FEN length the Android client emits.
    uci_history: comma-separated UCI moves (e.g. "e2e4,e7e5,g1f3").
        Bounded at 16 KB — enough for a 2000-move game which is
        well beyond any realistic length.
    """

    fen: str
    uci_history: str = ""

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        return _validate_fen_field(v)

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
        # from "doesn't exist / already finished" (404).  The repo
        # helper centralises the SQLAlchemy read so the endpoint
        # doesn't have to reach into the storage layer's session.
        from llm.seca.storage.repo import get_game_owner_status

        status = get_game_owner_status(game_id)
        if status is None:
            raise HTTPException(status_code=404, detail="game not found")
        owner_id, finished_at = status
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


# ------------------------------------------------------------------
# Explain endpoint (LLM layer comes next)
# ------------------------------------------------------------------


@app.post("/explain")
@limiter.limit("30/minute")
def explain(req: AnalyzeRequest, request: Request, player=Depends(get_current_player)):
    # FEN-only ESV via build_engine_signal — see PR 9 trust-boundary
    # fix: client-supplied stockfish_json must not reach the ESV
    # builder on a route the client can call.
    engine_signal = build_engine_signal(req)
    explanation = safe_explainer.explain(engine_signal)

    response = {
        "explanation": explanation,
        "engine_signal": engine_signal,
        "mode": "SAFE_V1",
    }
    validate_explain_response(response)
    return response


# ------------------------------------------------------------------
# Chat endpoint (long-form coaching conversation)
# ------------------------------------------------------------------


def _derive_player_profile(player) -> dict:
    """Build the ``player_profile`` dict the chat pipeline expects.

    ``chat_pipeline._build_context_block`` reads ``skill_estimate``,
    ``common_mistakes``, and ``strengths``.  The Android client
    historically sent a different shape (``{rating, confidence}``),
    so the pipeline silently produced an empty player block on every
    /chat call.  Building the dict server-side from the authenticated
    Player row fixes the contract mismatch AND removes the dependency
    on a freshly-cached client copy.

    Strictly deterministic.  No skill / rating / weakness state is
    WRITTEN here — those remain owned by
    ``llm.seca.skills.updater.SkillUpdater`` (triggered only by
    ``/game/finish`` per the SECA "no autonomous learning" rule).
    """
    skill_vector = {}
    if player.skill_vector_json:
        try:
            skill_vector = json.loads(player.skill_vector_json)
        except (json.JSONDecodeError, TypeError):
            skill_vector = {}

    # Top-3 weaknesses (highest scores in skill_vector) — items already
    # below a soft threshold are filtered so we don't expose a
    # placeholder "weakness" the player hasn't actually demonstrated.
    sorted_weak = sorted(
        ((k, float(v)) for k, v in skill_vector.items()),
        key=lambda kv: -kv[1],
    )
    common_mistakes = [name for name, score in sorted_weak[:3] if score > 0.3]

    # Rating → tier label.  Bands match
    # ``llm.seca.adaptation.adaptive.compute_adaptation`` style buckets
    # so the LLM gets a consistent skill signal across coach surfaces.
    rating = float(player.rating or 0.0)
    if rating < 1000:
        skill_estimate = "beginner"
    elif rating < 1600:
        skill_estimate = "intermediate"
    else:
        skill_estimate = "advanced"

    return {
        "skill_estimate": skill_estimate,
        "common_mistakes": common_mistakes,
        # Inverse signal isn't tracked separately today; empty list keeps
        # the pipeline's ``strengths`` branch on its no-strengths path.
        "strengths": [],
    }


def _derive_past_mistakes(player, limit: int = 5) -> list[str]:
    """Top-N weakness categories from the authenticated player.

    Same skill_vector source as ``_derive_player_profile`` but with a
    higher cap (5) and exposed at the top-level ``past_mistakes``
    field that ``generate_chat_reply`` consumes separately from
    ``player_profile``.
    """
    if not player.skill_vector_json:
        return []
    try:
        sv = json.loads(player.skill_vector_json)
    except (json.JSONDecodeError, TypeError):
        return []
    sorted_weak = sorted(
        ((k, float(v)) for k, v in sv.items()),
        key=lambda kv: -kv[1],
    )
    return [name for name, score in sorted_weak[:limit] if score > 0.3]


def _last_user_message(messages) -> str:
    """Return the most-recent ``role=user`` content in the request, or
    empty string when the history is malformed / contains no user turn.
    Used by the persistence path to record the question the user asked
    in this exchange."""
    for turn in reversed(messages):
        if turn.role == "user":
            return turn.content
    return ""


@app.post("/chat")
@limiter.limit("10/minute")
async def chat(
    req: ChatRequest,
    request: Request,
    player=Depends(get_current_player),
    db=Depends(get_db),
):
    """Mode-2: long-form coaching explanation for the LLM panel.

    LLM-powered with conversation history, RAG, and Mode-2 validation;
    falls back to deterministic reply when Ollama is unavailable.
    Runs in a thread-pool executor so the async event loop is not blocked.

    Chat history is persisted server-side as a side-effect of a 2xx
    response (see ``llm.seca.chat.repo.save_exchange``).  Both the
    user message and the final reply (LLM or deterministic fallback,
    whichever the user actually saw) are written in a single
    transaction after boundary validation succeeds.  Saves on the
    failure paths (5xx, validator rejection of even the deterministic
    fallback) intentionally do NOT happen — the user will retry and
    the next successful exchange replaces the lost turn.
    """
    # Server-derived context replaces ``req.player_profile`` and
    # ``req.past_mistakes`` so the coach sees authoritative player
    # state, not a possibly-stale client cache.  Request-body fields
    # are kept in the schema for backwards compatibility (old Android
    # clients still send them); they are ignored at this layer.
    derived_profile = _derive_player_profile(player)
    derived_past_mistakes = _derive_past_mistakes(player)

    turns = [_ChatPipelineTurn(role=t.role, content=t.content) for t in req.messages]
    result = await asyncio.to_thread(
        generate_chat_reply,
        req.fen,
        turns,
        derived_profile,
        derived_past_mistakes,
        req.move_count,
        req.coach_voice,
    )
    response = {
        "reply": result.reply,
        "engine_signal": result.engine_signal,
        "mode": result.mode,
    }
    try:
        validate_chat_response(response)
    except ExplainSchemaError as exc:
        # Defense-in-depth — the pipeline already runs every Mode-2 gate
        # the boundary re-runs (negative + structure + semantic), so an
        # ExplainSchemaError here means a validator drift the pipeline
        # didn't catch.  Returning 500 surfaces to the client as "Coach
        # is offline".  Swap in the deterministic reply — constructed
        # to satisfy every gate by construction — and log so the drift
        # is fixable without losing a user session.
        logger.warning(
            "validate_chat_response rejected pipeline reply (%s); "
            "substituting deterministic fallback",
            exc,
        )
        fallback = await asyncio.to_thread(
            generate_chat_reply,
            req.fen,
            turns,
            derived_profile,
            derived_past_mistakes,
            req.move_count,
            req.coach_voice,
            True,  # force_deterministic — skip LLM, emit hand-tuned fallback
        )
        response = {
            "reply": fallback.reply,
            "engine_signal": fallback.engine_signal,
            "mode": fallback.mode,
        }
        validate_chat_response(response)

    # Persist the exchange — server-authoritative chat history.
    # ``_last_user_message`` walks the request's message list to find
    # the user's question for this exchange; the assistant content is
    # the FINAL reply the user saw (deterministic-fallback included).
    # Save failures are best-effort: a DB hiccup must not 500 a
    # successfully-validated coaching response.
    try:
        save_exchange(
            db=db,
            player_id=str(player.id),
            user_content=_last_user_message(req.messages),
            assistant_content=response["reply"],
            fen=req.fen,
            mode=response["mode"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "chat history save failed (%s: %s); reply already returned",
            type(exc).__name__,
            exc,
        )
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
    db=Depends(get_db),
):
    """Streaming variant of POST /chat — same LLM pipeline, chunked via Server-Sent Events.

    Emits one SSE event per word of the coaching reply, then a final ``done``
    event carrying ``engine_signal`` and ``mode``.  Wire format::

        data: {"type": "chunk", "text": "<word> "}\n\n
        ...
        data: {"type": "done", "engine_signal": {...}, "mode": "CHAT_V1"}\n\n

    Uses the same LLM-powered chat_pipeline.generate_chat_reply(); no RL.
    The pipeline runs in a thread-pool executor so the event loop is not blocked.

    Chat history is persisted via ``save_exchange`` after boundary
    validation succeeds — same contract as ``/chat`` above.
    """
    derived_profile = _derive_player_profile(player)
    derived_past_mistakes = _derive_past_mistakes(player)

    turns = [_ChatPipelineTurn(role=t.role, content=t.content) for t in req.messages]
    result = await asyncio.to_thread(
        generate_chat_reply,
        req.fen,
        turns,
        derived_profile,
        derived_past_mistakes,
        req.move_count,
        req.coach_voice,
    )

    # Boundary validation runs before any bytes are streamed so a contract
    # failure surfaces as a clean 500 from FastAPI, not a half-delivered
    # SSE stream the client has to parse to discover the failure.
    try:
        validate_chat_response(
            {
                "reply": result.reply,
                "engine_signal": result.engine_signal,
                "mode": result.mode,
            }
        )
    except ExplainSchemaError as exc:
        # Defense-in-depth twin of the /chat branch above.  Same
        # rationale: an ExplainSchemaError here means a drift between
        # the in-pipeline gates and the boundary validator; rather than
        # surfacing "Coach is offline" to the user, re-run the pipeline
        # deterministically so the resulting reply passes by
        # construction.
        logger.warning(
            "validate_chat_response rejected pipeline reply on /chat/stream "
            "(%s); substituting deterministic fallback",
            exc,
        )
        result = await asyncio.to_thread(
            generate_chat_reply,
            req.fen,
            turns,
            derived_profile,
            derived_past_mistakes,
            req.move_count,
            req.coach_voice,
            True,  # force_deterministic — skip LLM, emit hand-tuned fallback
        )
        validate_chat_response(
            {
                "reply": result.reply,
                "engine_signal": result.engine_signal,
                "mode": result.mode,
            }
        )

    # Persist the exchange BEFORE returning the StreamingResponse so a
    # crash during SSE-iteration doesn't leave the row missing.  Same
    # best-effort contract as ``/chat``.
    try:
        save_exchange(
            db=db,
            player_id=str(player.id),
            user_content=_last_user_message(req.messages),
            assistant_content=result.reply,
            fen=req.fen,
            mode=result.mode,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "chat/stream history save failed (%s: %s); reply still streamed",
            type(exc).__name__,
            exc,
        )

    # Take ownership of the JWT rotation from the
    # ``commit_pending_auth_rotation`` middleware.  The middleware's
    # default eager-commit model fires the DB write BEFORE the
    # response body starts streaming — for a non-streaming JSON
    # route the gap to "headers on the wire" is microseconds and the
    # ``previous_token_hash`` 10 s grace window absorbs any TCP drop
    # in that window.  For a long-running SSE stream the same eager
    # commit still happens microseconds before ASGI flushes the
    # headers, but the perception is asymmetric: a stream that
    # subsequently fails mid-transmission is much more common than
    # a fast-route failure.  By popping the pending rotation here
    # we tell the middleware "this handler owns the rotation
    # lifecycle"; we set ``X-Auth-Token`` on the response header
    # synchronously (so it ships with the first wire bytes), and we
    # commit the DB rotation INSIDE the generator after the first
    # chunk has been yielded.  At that point ASGI has handed the
    # first body bytes to the socket and the headers are
    # demonstrably out — narrowing the "client received the new
    # token but server has not yet rotated" race to microseconds
    # within which the previous_token_hash grace window is more
    # than enough.
    pending_rotation = getattr(request.state, "pending_auth_rotation", None)
    if pending_rotation is not None:
        # Clear from request.state so the middleware sees None and
        # skips its commit path.
        try:
            del request.state.pending_auth_rotation
        except AttributeError:
            # Starlette ``State`` is a plain attrdict; del should
            # always work, but stay defensive against future
            # framework changes.
            pass

    def _generate():
        words = result.reply.split(" ")
        rotation_committed = False
        for i, word in enumerate(words):
            text = word if i == len(words) - 1 else word + " "
            yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
            if not rotation_committed and pending_rotation is not None:
                # First chunk has been handed to ASGI for socket
                # transmission; the response headers (including
                # X-Auth-Token) preceded it.  Safe to land the DB
                # rotation now — a stream failure before this point
                # leaves the old token's hash in place, so the
                # client's next call with its previously-known
                # token still validates.
                rotation_committed = True
                try:
                    # Deferred import mirrors the middleware path —
                    # avoids importing auth.router at server.py
                    # module-import time before lifespan has run
                    # init_schema.
                    from llm.seca.auth.router import (  # noqa: PLC0415
                        SessionLocal as _SessionLocal,
                    )
                    from llm.seca.auth.service import (  # noqa: PLC0415
                        AuthService as _AuthService,
                    )

                    with _SessionLocal() as _db_rot:
                        _AuthService(_db_rot).rotate_session_token(
                            pending_rotation["session_id"],
                            pending_rotation["new_token"],
                        )
                except Exception as exc:  # noqa: BLE001
                    # Match the middleware's behaviour on commit
                    # failure: log and continue.  The client has
                    # already received X-Auth-Token (header is on
                    # the wire), so a DB hiccup here means server
                    # token_hash stays at the OLD value while client
                    # has the NEW — the previous_token_hash grace
                    # window does not cover this case, so the user
                    # will re-login on their next call.  Rare
                    # (single UPDATE + commit) and recoverable.
                    logger.warning(
                        "post-stream-start auth rotation commit "
                        "failed (%s: %s) — client received new "
                        "token but server hash unchanged; "
                        "expecting one re-login",
                        type(exc).__name__,
                        exc,
                    )
        yield f"data: {json.dumps({'type': 'done', 'engine_signal': result.engine_signal, 'mode': result.mode})}\n\n"

    response = StreamingResponse(_generate(), media_type="text/event-stream")
    if pending_rotation is not None:
        # Pre-populate the rotation header so it ships with the
        # initial response headers, before the first chunk yields.
        # The Android client consumes this header via
        # ``consumeRefreshedToken`` in ``CoachApiClient.kt`` right
        # after parsing the response code, before reading the body.
        response.headers["X-Auth-Token"] = pending_rotation["new_token"]
    return response


# ------------------------------------------------------------------
# Chat history (server-authoritative recall for the Android client)
# ------------------------------------------------------------------


@app.get("/chat/history")
@limiter.limit("30/minute")
async def chat_history(
    request: Request,
    limit: int = HISTORY_DEFAULT_LIMIT,
    player=Depends(get_current_player),
    db=Depends(get_db),
):
    """Return up to ``limit`` recent chat turns for the authenticated player.

    The Android client calls this on ``ChatBottomSheet.onAttach`` to
    seed its in-memory ``ChatSessionStore`` so a conversation survives
    process restarts, device swaps, and reinstalls.

    Cross-player isolation is by ``WHERE player_id = ?`` in the repo
    layer; the route is Bearer-only via ``get_current_player`` so the
    inbound player_id is the authenticated one — no client-supplied
    player filter is accepted.

    Response is chronological (oldest first) so the client can
    ``addAll`` directly into its message list without re-ordering.
    """
    bounded = max(1, min(int(limit), HISTORY_MAX_LIMIT))
    rows = recent_turns_for_player(db, str(player.id), limit=bounded)
    # Repo returns DESC (newest first); reverse for client consumption.
    rows = list(reversed(rows))
    return {
        "turns": [
            {
                "id": r.id,
                "role": r.role,
                "content": r.content,
                "fen": r.fen,
                "mode": r.mode,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }
