from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import cast

import chess
import chess.engine

from .board_features import compute_position_flags, compute_tactical_flags

logger = logging.getLogger(__name__)

# ``redis`` is an optional dependency.  The ``# type: ignore[assignment]``
# is intentional: when the import fails, ``redis`` is reassigned to
# ``None``, producing a ``Module | None`` union mypy rejects under
# strict assignment-narrowing.  Every caller below guards on
# ``redis is not None`` before touching it, so the ignore is locally
# scoped and the runtime behaviour is unchanged.
# ``redis`` is a module name, not a constant — the ``invalid-name``
# disable below is the pylint mark for the ``redis = None`` fallback
# (pylint sees the module-shadow as a constant assignment).
# pylint: disable=invalid-name
try:
    import redis  # noqa: F401  — re-exported as ``redis`` for the guards below
except Exception:  # pragma: no cover - optional dependency
    redis = None  # type: ignore[assignment]
# pylint: enable=invalid-name


# An engine move read back from Redis must look like a UCI move (e.g. ``e2e4``,
# ``e7e8q``).  Anything else — a poisoned or corrupted cache entry written by
# another process sharing the Redis instance — is treated as a cache miss so
# the caller recomputes from the trusted engine rather than serving an
# attacker-chosen string to the user as a move.
_UCI_MOVE_RE = re.compile(r"[a-h][1-8][a-h][1-8][qrbnQRBN]?")


@dataclass(frozen=True)
class EnginePoolSettings:
    stockfish_path: str
    pool_size: int = 8
    threads: int = 1
    hash_mb: int = 128
    skill_level: int = 10
    default_movetime_ms: int = 40
    training_movetime_ms: int = 40
    analysis_movetime_ms: int = 80
    blitz_movetime_ms: int = 25
    queue_timeout_ms: int = 50
    min_movetime_ms: int = 20
    max_movetime_ms: int = 2000


# Cache-key version. Bump when the digest layout changes; that alone
# invalidates every previously-stored entry across all engine configs.
# Per-config invalidation is handled separately by the fingerprint computed
# in engine_config_fingerprint() — that hashes the EnginePoolSettings
# fields that affect engine *output*, so a config change drops in a fresh
# cache namespace without needing a manual version bump.
_CACHE_VERSION = "v3"
_DEFAULT_NAMESPACE = f"fen_move:{_CACHE_VERSION}"


def engine_config_fingerprint(
    settings: EnginePoolSettings,
    *,
    override: str | None = None,
) -> str:
    """Stable 12-char hash of engine settings — included in the cache keys
    of both engine caches (``FenMoveCache`` moves, ``FenEvalCache`` evals).

    Any change to the fields below produces a new fingerprint, which lands
    every cache lookup in a fresh namespace and effectively invalidates
    stale entries that were computed under different settings. This is the
    answer to "what happens when ENGINE_SKILL_LEVEL changes?" — the cache
    misses, gets recomputed under the new config, and the old entries TTL
    out without ever being served.

    Fields hashed
    -------------
    stockfish_path, threads, hash_mb, skill_level, all four mode-specific
    movetime defaults, the min/max movetime clamps, and the optional
    operator-supplied override (or `ENGINE_CACHE_VERSION` env var).

    Stockfish *binary version* is intentionally NOT introspected here —
    that would require running the engine to read `engine.id`, coupling
    cache-key construction to live process state. Operators who replace
    the binary in place (same path, different version) should bump
    `ENGINE_CACHE_VERSION` to force a flush. Documented in
    docs/OPERATIONS.md.
    """
    resolved_override = override if override is not None else os.getenv("ENGINE_CACHE_VERSION", "")
    parts = (
        f"sf:{settings.stockfish_path}",
        f"th:{settings.threads}",
        f"hash:{settings.hash_mb}",
        f"skill:{settings.skill_level}",
        f"mt_def:{settings.default_movetime_ms}",
        f"mt_train:{settings.training_movetime_ms}",
        f"mt_anal:{settings.analysis_movetime_ms}",
        f"mt_blitz:{settings.blitz_movetime_ms}",
        f"mt_min:{settings.min_movetime_ms}",
        f"mt_max:{settings.max_movetime_ms}",
        f"override:{resolved_override}",
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


class FenMoveCache:
    def __init__(  # pylint: disable=redefined-outer-name
        self,
        *,
        redis_url: str | None,
        ttl_seconds: int = 3600,
        namespace: str = _DEFAULT_NAMESPACE,
        max_memory_items: int = 500,
        engine_config_fingerprint: str = "",
    ):
        # The ``engine_config_fingerprint`` parameter intentionally
        # shadows the module-level ``engine_config_fingerprint``
        # function name — callers pass the fingerprint VALUE, which
        # is the result of calling the function.  Renaming the
        # parameter would be a breaking change for every caller (the
        # parameter is keyword-only).  pylint disable scoped to this
        # method only.
        self._ttl_seconds = ttl_seconds
        self._namespace = namespace
        self._engine_config_fingerprint = engine_config_fingerprint
        self._max_memory_items = max(1, max_memory_items)
        # L1 cache: bounded local hotset for fastest path.
        self._memory_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._redis = None

        if redis_url and redis is not None:
            try:
                client = redis.Redis.from_url(redis_url)
                client.ping()
                self._redis = client
            except Exception:
                self._redis = None

    def _cache_key(  # pylint: disable=unused-argument
        self,
        *,
        fen: str,
        mode: str,
        movetime_ms: int,
        target_elo: int | None,
        line_key: str | None = None,
    ) -> str:
        # ``movetime_ms`` is intentionally part of the signature for
        # call-site symmetry with ``get`` / ``set`` but is NOT included
        # in the cache-key digest: positions cached at one movetime
        # are reused for any subsequent movetime.  The cache-key
        # equivalence class is documented in docs/API_CONTRACTS.md.
        # The pylint disable above is scoped to this method only.
        #
        # Keep key coarse for movetime, but include line_key to disambiguate
        # equivalent FEN requests coming from different move-line contexts.
        # Engine config fingerprint participates in the digest so config
        # changes (e.g. ENGINE_SKILL_LEVEL) automatically invalidate stale
        # entries — see engine_config_fingerprint() above for the rationale.
        digest = hashlib.sha256(
            (
                f"{fen}|{mode}|{target_elo}|" f"{line_key or '-'}|{self._engine_config_fingerprint}"
            ).encode("utf-8")
        ).hexdigest()
        return f"{self._namespace}:{digest}"

    def get(
        self,
        *,
        fen: str,
        mode: str,
        movetime_ms: int,
        target_elo: int | None,
        line_key: str | None = None,
    ) -> str | None:
        key = self._cache_key(
            fen=fen,
            mode=mode,
            movetime_ms=movetime_ms,
            target_elo=target_elo,
            line_key=line_key,
        )

        if self._redis is not None:
            try:
                # ``redis-py``'s stubs surface ``Redis.get`` as a union
                # of sync (returns ``bytes | None``) and async (returns
                # ``Awaitable[Any]``) signatures because the same class
                # name is reused for both clients.  ``self._redis`` is a
                # sync client (constructed via ``Redis.from_url`` above),
                # so the runtime value is ``bytes | None``.  ``cast``
                # picks the right side without a runtime cost.
                cached = cast("bytes | None", self._redis.get(key))
                if cached:
                    move = cached.decode("utf-8")
                    # Validate the shape before serving it: a poisoned Redis
                    # entry must never reach a caller as an engine move. A
                    # non-UCI value falls through to the L1/engine path.
                    if _UCI_MOVE_RE.fullmatch(move):
                        return move
            except Exception:
                pass

        now = time.time()
        with self._lock:
            # L1 fallback.  Renamed from ``cached`` (used above for the
            # Redis path) so mypy doesn't latch the variable's type at
            # ``bytes | None`` and reject the L1 tuple shape.
            entry = self._memory_cache.get(key)
            if not entry:
                return None

            value, expires_at = entry
            if expires_at < now:
                self._memory_cache.pop(key, None)
                return None
            self._memory_cache.move_to_end(key)
            return value

    def set(
        self,
        *,
        fen: str,
        mode: str,
        movetime_ms: int,
        target_elo: int | None,
        move_uci: str,
        line_key: str | None = None,
    ) -> None:
        key = self._cache_key(
            fen=fen,
            mode=mode,
            movetime_ms=movetime_ms,
            target_elo=target_elo,
            line_key=line_key,
        )

        if self._redis is not None:
            try:
                self._redis.set(key, move_uci, ex=self._ttl_seconds)
                return
            except Exception:
                pass

        with self._lock:
            self._memory_cache[key] = (move_uci, time.time() + self._ttl_seconds)
            self._memory_cache.move_to_end(key)
            while len(self._memory_cache) > self._max_memory_items:
                self._memory_cache.popitem(last=False)


# ---------------------------------------------------------------------------
# Position-evaluation cache
# ---------------------------------------------------------------------------

#: Eval-cache namespace version.  Bump when the payload layout or the
#: position-key derivation changes; that alone invalidates every stored
#: entry.  Per-config invalidation is handled by the engine-config
#: fingerprint participating in the key digest — same split as the move
#: cache above.
_EVAL_CACHE_VERSION = "v1"
_EVAL_DEFAULT_NAMESPACE = f"fen_eval:{_EVAL_CACHE_VERSION}"

#: Entries must survive from a game's FIRST live-move evaluation to its
#: /game/finish recompute — games run 10-30 minutes, resumed games
#: longer.  Six hours keeps a same-session resume warm; anything older
#: falls back to a fresh engine pass (correct, just slower).
_EVAL_TTL_SECONDS = 6 * 3600

#: L1 bound.  A full game touches ≤ ~400 entries (two evaluations per
#: ply, 200-ply analysis cap) at ~100 bytes each; 4096 covers every
#: concurrently-live game a single worker realistically serves.
_EVAL_MAX_MEMORY_ITEMS = 4096

#: Bounds a cached payload must satisfy before it is served.  Stockfish
#: cp scores stay within ±~2000 for non-mate positions and the callers'
#: mate collapse uses ±10000 (``pgn_accuracy._MATE_VALUE_CP``); anything
#: outside these bounds is a corrupted or poisoned entry and is treated
#: as a miss, mirroring the ``_UCI_MOVE_RE`` posture of the move cache.
_EVAL_MAX_ABS_CP = 20_000
_EVAL_MAX_ABS_MATE = 500


class FenEvalCache:
    """Cache for ``StockfishEnginePool.evaluate_position`` scores.

    Why this exists: the positions of a live game are engine-evaluated
    move by move while it is being played (``/live/move`` runs a 200 ms
    ``evaluate_position`` on both the pre-move and post-move FEN of
    every player move), and then the ``/game/finish`` accuracy recompute
    re-evaluated every one of them again in a single synchronous batch —
    (plies + 1) × 200 ms, i.e. the 10-20 s the post-game summary sheet
    used to hang before appearing.  Serving the finish sweep from the
    evaluations the live path already paid for collapses that batch to
    the handful of positions live play never saw (typically just the
    final position when the opponent moved last).

    Trust boundary: values enter the cache exclusively from local engine
    output — the same trust level as a fresh ``analyse`` call.  Redis
    round-trips are validated on read (type/value shape and bounds), so
    a corrupted or poisoned entry degrades to a cache miss and a fresh
    engine evaluation, never to a served fake score.

    Key semantics: entries are keyed on the position's EPD (piece
    placement, side to move, castling rights, and the *legal* en-passant
    square) plus the exact ``movetime_ms`` and the engine-config
    fingerprint.  EPD rather than the raw FEN because the same position
    reaches the pool spelled differently — the Android client's FEN
    (live play) and python-chess's PGN-replay FEN (finish recompute) can
    disagree on move counters and on cosmetic (non-capturable)
    en-passant fields, and keying on them would only manufacture
    misses.  Dropping the counters is a deliberate, bounded imprecision:
    an eval cached under one halfmove clock can be served under another,
    which matters only in 50-move-rule-adjacent endgames — noise well
    inside the 50 cp classification granularity every consumer uses.
    ``movetime_ms`` stays in the key — unlike the move cache's movetime
    equivalence class — because the analysis budget IS the eval's
    quality level (the 200 ms /game/finish budget was a deliberate
    depth fix; a 150 ms chat eval must not satisfy it).
    """

    def __init__(  # pylint: disable=redefined-outer-name
        self,
        *,
        redis_url: str | None,
        ttl_seconds: int = _EVAL_TTL_SECONDS,
        namespace: str = _EVAL_DEFAULT_NAMESPACE,
        max_memory_items: int = _EVAL_MAX_MEMORY_ITEMS,
        engine_config_fingerprint: str = "",
    ):
        # ``engine_config_fingerprint`` shadows the module-level function
        # of the same name for the same call-site-symmetry reason
        # documented on ``FenMoveCache.__init__`` — callers pass the
        # fingerprint VALUE.
        self._ttl_seconds = ttl_seconds
        self._namespace = namespace
        self._engine_config_fingerprint = engine_config_fingerprint
        self._max_memory_items = max(1, max_memory_items)
        self._memory_cache: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._redis = None

        if redis_url and redis is not None:
            try:
                client = redis.Redis.from_url(redis_url)
                client.ping()
                self._redis = client
            except Exception:
                self._redis = None

    def _cache_key(self, *, position_key: str, movetime_ms: int) -> str:
        digest = hashlib.sha256(
            (f"{position_key}|mt:{movetime_ms}|{self._engine_config_fingerprint}").encode("utf-8")
        ).hexdigest()
        return f"{self._namespace}:{digest}"

    @staticmethod
    def _validated(evaluation: object) -> dict | None:
        """Return a normalised copy iff *evaluation* is a plausible engine
        score — ``{"type": "cp" | "mate", "value": int-within-bounds}``.
        Anything else (poisoned Redis entry, schema drift) returns None so
        the caller treats it as a miss.  The copy also insulates the L1
        store from callers that mutate the returned dict.
        """
        if not isinstance(evaluation, dict):
            return None
        eval_type = evaluation.get("type")
        value = evaluation.get("value")
        # bool is an int subclass — exclude it explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if eval_type == "cp":
            if abs(value) > _EVAL_MAX_ABS_CP:
                return None
        elif eval_type == "mate":
            if abs(value) > _EVAL_MAX_ABS_MATE:
                return None
        else:
            return None
        return {"type": eval_type, "value": value}

    def get(self, *, board: chess.Board, movetime_ms: int) -> dict | None:
        key = self._cache_key(position_key=board.epd(), movetime_ms=movetime_ms)

        if self._redis is not None:
            try:
                # Same sync-client ``cast`` rationale as FenMoveCache.get.
                cached = cast("bytes | None", self._redis.get(key))
                if cached:
                    validated = self._validated(json.loads(cached))
                    if validated is not None:
                        return validated
            except Exception:
                pass

        now = time.time()
        with self._lock:
            entry = self._memory_cache.get(key)
            if not entry:
                return None
            value, expires_at = entry
            if expires_at < now:
                self._memory_cache.pop(key, None)
                return None
            self._memory_cache.move_to_end(key)
        # Validate outside the lock; L1 entries were validated on write,
        # so this is belt-and-braces plus the mutation-insulating copy.
        return self._validated(value)

    def set(self, *, board: chess.Board, movetime_ms: int, evaluation: dict) -> None:
        validated = self._validated(evaluation)
        if validated is None:
            # Never persist a shape we would refuse to serve.
            return
        key = self._cache_key(position_key=board.epd(), movetime_ms=movetime_ms)

        if self._redis is not None:
            try:
                self._redis.set(key, json.dumps(validated), ex=self._ttl_seconds)
                return
            except Exception:
                pass

        with self._lock:
            self._memory_cache[key] = (validated, time.time() + self._ttl_seconds)
            self._memory_cache.move_to_end(key)
            while len(self._memory_cache) > self._max_memory_items:
                self._memory_cache.popitem(last=False)


class StockfishEnginePool:
    #: Consecutive failed operations after which a process-alive engine is
    #: evicted anyway (audit 2026-07-14, P2 #6).  The transport probe in
    #: ``_is_engine_alive`` only catches DEAD subprocesses; a hung-but-alive
    #: Stockfish (internal deadlock, stopped reading stdin) passes it forever
    #: and keeps cycling through the pool, timing out every request it
    #: serves.  Three strikes tolerates transient load-induced timeouts (a
    #: false eviction merely costs one ~50ms respawn) while bounding how
    #: long a wedged engine can poison its slot.
    MAX_CONSECUTIVE_ENGINE_FAILURES = 3

    def __init__(
        self,
        settings: EnginePoolSettings,
        *,
        eval_cache: FenEvalCache | None = None,
    ):
        self.settings = settings
        #: Optional read-through cache for ``evaluate_position`` — see
        #: :class:`FenEvalCache`.  None (tests, ad-hoc pools) preserves
        #: the uncached behaviour: every call runs a fresh ``analyse``.
        self.eval_cache = eval_cache
        self._engines: queue.Queue[chess.engine.SimpleEngine] = queue.Queue(
            maxsize=settings.pool_size
        )
        self._started = False
        self._lock = threading.Lock()
        # id(engine) -> consecutive failed operations.  Only the thread
        # holding an engine (queue ownership is exclusive) touches that
        # engine's entry, so no extra locking is needed; entries are
        # popped on success, death, or eviction so the dict cannot grow
        # past pool_size live handles.
        self._consecutive_failures: dict[int, int] = {}

    def startup(self) -> None:
        with self._lock:
            if self._started:
                return
            for _ in range(self.settings.pool_size):
                self._engines.put(self._spawn_engine())
            self._started = True

    def qsize(self) -> int:
        return self._engines.qsize()

    def _spawn_engine(self) -> chess.engine.SimpleEngine:
        engine = chess.engine.SimpleEngine.popen_uci(self.settings.stockfish_path)
        options = {
            "Threads": self.settings.threads,
            "Hash": self.settings.hash_mb,
            "Skill Level": self.settings.skill_level,
            "MultiPV": 1,
        }
        try:
            engine.configure(options)
        except chess.engine.EngineError:
            # Engines can reject MultiPV in managed modes; keep core tuning.
            options.pop("MultiPV", None)
            engine.configure(options)
        return engine

    def close(self) -> None:
        with self._lock:
            if not self._started:
                return
            while not self._engines.empty():
                engine = self._engines.get_nowait()
                try:
                    engine.quit()
                except Exception:
                    pass
            self._started = False

    def _release_engine(
        self,
        engine: chess.engine.SimpleEngine | None,
        *,
        healthy: bool = True,
    ) -> None:
        """Return *engine* to the pool, or replace it if it is unusable.

        ``select_move``'s ``finally`` used to call ``self._engines.put(engine)``
        unconditionally.  When the Stockfish subprocess crashed during
        ``engine.play(...)`` (SIGSEGV on a malformed UCI command, OOM,
        kernel kill), the dead handle went back into the queue and the
        next acquirer pulled a corpse — second-order failures across
        the next pool_size requests until the queue cycled out.

        This helper is the central release point, with two eviction
        triggers:

        * DEAD — the cheap transport probe says the subprocess is gone:
          replace immediately (the original crash-recovery path).
        * HUNG (audit 2026-07-14, P2 #6) — the process is alive but the
          operation FAILED (``healthy=False`` from the caller: timeout,
          EngineError, no-move).  A wedged engine passes the transport
          probe forever, so after ``MAX_CONSECUTIVE_ENGINE_FAILURES``
          consecutive failed operations the handle is killed and
          replaced anyway.  A success resets the strike count.

        If the replacement spawn itself fails (binary missing, system
        out of file descriptors), the slot is forfeited with a WARNING —
        the alternative would be to deadlock the pool waiting for a
        healthy spawn that may never come, which is worse than running
        with one fewer engine until the operator restarts.
        """
        if engine is None:
            return

        if not self._is_engine_alive(engine):
            self._consecutive_failures.pop(id(engine), None)
            logger.warning("Stockfish subprocess died during a request; recycling pool slot")
            self._replace_engine_slot(engine)
            return

        if healthy:
            self._consecutive_failures.pop(id(engine), None)
            self._engines.put(engine)
            return

        strikes = self._consecutive_failures.get(id(engine), 0) + 1
        if strikes < self.MAX_CONSECUTIVE_ENGINE_FAILURES:
            self._consecutive_failures[id(engine)] = strikes
            self._engines.put(engine)
            return

        self._consecutive_failures.pop(id(engine), None)
        logger.warning(
            "Stockfish engine failed %d consecutive operations while its "
            "process stayed alive; evicting the hung handle and recycling "
            "the pool slot",
            strikes,
        )
        self._replace_engine_slot(engine)

    def _replace_engine_slot(self, engine: chess.engine.SimpleEngine) -> None:
        """Dispose of *engine* and put a fresh spawn in its slot (best effort)."""
        # A HUNG engine's ``quit()`` can itself block on the unread UCI
        # pipe — kill the transport first so quit returns immediately
        # (raises EngineTerminatedError on an already-dead process;
        # swallowed either way).
        transport = getattr(engine, "transport", None)
        if transport is not None:
            try:
                transport.kill()
            except Exception:
                pass
        try:
            engine.quit()
        except Exception:
            pass

        try:
            replacement = self._spawn_engine()
        except Exception:
            logger.exception(
                "Stockfish respawn failed; pool slot forfeited "
                "until next process restart (queue size now %d/%d)",
                self._engines.qsize(),
                self.settings.pool_size,
            )
            return

        self._engines.put(replacement)

    def _is_engine_alive(self, engine: chess.engine.SimpleEngine) -> bool:
        """Cheap liveness probe — does *not* invoke a UCI command.

        ``chess.engine.SimpleEngine`` exposes ``transport`` (the
        asyncio transport bound to the subprocess pipes).  A closed
        transport means the subprocess has exited; that is the only
        signal we need to decide "this handle is a corpse, drop it".
        We deliberately avoid sending a UCI ``isready`` here because
        (a) it costs a round-trip on every release and (b) on a
        partially-broken engine it could itself raise and obscure the
        original exception the caller is seeing.
        """
        transport = getattr(engine, "transport", None)
        if transport is None:
            # SimpleEngine constructed in tests without a real
            # transport — treat as alive (the test owns liveness).
            return True
        try:
            return not transport.is_closing()
        except Exception:
            return False

    def resolve_movetime_ms(self, mode: str, movetime_ms: int | None) -> int:
        if movetime_ms is not None:
            ms = movetime_ms
        else:
            normalized = (mode or "default").lower()
            mode_map = {
                "blitz": self.settings.blitz_movetime_ms,
                "training": self.settings.training_movetime_ms,
                "analysis": self.settings.analysis_movetime_ms,
                # Backward-compatible aliases.
                "puzzle": self.settings.training_movetime_ms,
                "deep": self.settings.analysis_movetime_ms,
            }
            ms = mode_map.get(normalized, self.settings.default_movetime_ms)

        if ms < self.settings.min_movetime_ms:
            return self.settings.min_movetime_ms
        if ms > self.settings.max_movetime_ms:
            return self.settings.max_movetime_ms
        return ms

    def _apply_runtime_options(
        self,
        engine: chess.engine.SimpleEngine,
        *,
        target_elo: int | None,
    ) -> None:
        if target_elo is None:
            return
        try:
            engine.configure({"UCI_LimitStrength": True, "UCI_Elo": int(target_elo)})
        except chess.engine.EngineTerminatedError:
            # Subprocess died while we were configuring it — let this
            # propagate so the surrounding ``finally`` in select_move
            # routes the dead handle through ``_release_engine`` and
            # respawns instead of swallowing the death signal here.
            raise
        except chess.engine.EngineError:
            # Not all Stockfish builds expose ELO limiting; silent
            # downgrade is fine when the engine is otherwise healthy.
            pass

    def fast_fallback_move(self, board: chess.Board) -> chess.Move:
        legal_moves = list(board.legal_moves)
        if not legal_moves:
            raise RuntimeError("No legal moves available")

        captures = [mv for mv in legal_moves if board.is_capture(mv)]
        candidates = captures or legal_moves
        # Stable and cheap fallback: deterministic lexical move ordering.
        return min(candidates, key=lambda mv: mv.uci())

    def select_move(
        self,
        *,
        fen: str,
        board: chess.Board | None = None,
        moves_uci: list[str] | None = None,
        mode: str = "default",
        movetime_ms: int | None = None,
        queue_timeout_ms: int | None = None,
        target_elo: int | None = None,
    ) -> chess.Move:
        if not self._started:
            raise RuntimeError("Engine pool not started")

        resolved_board = board
        if resolved_board is None:
            resolved_board = chess.Board(fen)
            if moves_uci:
                # For start-position games this preserves move stack and can send
                # `position startpos moves ...` to UCI engines.
                candidate = chess.Board()
                try:
                    for move_uci in moves_uci:
                        candidate.push_uci(move_uci)
                    if candidate.fen() == fen:
                        resolved_board = candidate
                except ValueError:
                    resolved_board = chess.Board(fen)

        timeout_ms = queue_timeout_ms
        if timeout_ms is None:
            timeout_ms = self.settings.queue_timeout_ms
        if timeout_ms <= 0:
            timeout_ms = 1

        try:
            engine = self._engines.get(timeout=timeout_ms / 1000.0)
        except queue.Empty as exc:
            raise RuntimeError(f"Stockfish queue wait exceeded {timeout_ms}ms") from exc
        # The release path goes through ``_release_engine`` so a
        # crashed subprocess (raised as ``EngineTerminatedError`` or
        # leaving a closed transport behind) is detected and the slot
        # is repopulated with a fresh engine instead of a corpse.  The
        # ``healthy`` flag feeds the hung-engine strike counter: it only
        # flips True once the operation fully succeeded, so any raise —
        # timeout, EngineError, no-move — counts as a strike against
        # this handle (P2 #6).
        healthy = False
        try:
            self._apply_runtime_options(engine, target_elo=target_elo)
            limit = chess.engine.Limit(time=self.resolve_movetime_ms(mode, movetime_ms) / 1000.0)
            result = engine.play(resolved_board, limit)
            if result.move is None:
                raise RuntimeError("Stockfish returned no move")
            healthy = True
            return result.move
        finally:
            self._release_engine(engine, healthy=healthy)

    def evaluate_position(
        self,
        *,
        fen: str,
        movetime_ms: int = 200,
        queue_timeout_ms: int | None = None,
    ) -> dict:
        """Run Stockfish on a single position and return a stockfish_json dict.

        Returns the shape that ``extract_engine_signal`` consumes:
            {
                "evaluation":     {"type": "cp" | "mate", "value": int},
                "tactical_flags": list[str],
                "position_flags": list[str],
            }

        ``value`` is centipawns from White's perspective for "cp", or the
        signed mate-in-N for "mate" (positive = White mates, negative =
        Black mates).  Always returns a dict; on any engine failure the
        function raises so the caller can decide whether to fall back
        to the heuristic-only path.

        ``tactical_flags`` and ``position_flags`` are computed
        deterministically from the current board by
        ``llm.seca.engines.stockfish.board_features`` and are populated
        unconditionally (no extra Stockfish search) — see ARCHITECTURE.md
        §72-95 for the trust-boundary rules they live under.  Strings
        are drawn from the closed vocabulary in
        ``llm.rag.engine_signal.flag_vocabulary``.

        Callers: the Mode-1 ``/live/move`` route (which uses the result
        to give the LLM real tactical context — band, side, mate flag —
        instead of the FEN-only heuristic ``extract_engine_signal``
        derives when ``stockfish_json`` is empty; see PR #87), the /chat
        eval, the /game/finish accuracy recompute, and the imported-game
        review pipeline.

        When the pool carries a :class:`FenEvalCache`, the score is
        served from it whenever this position was already evaluated at
        the same ``movetime_ms`` under the same engine config — the
        board-derived flag lists are recomputed either way (they are
        deterministic and engine-free).  This is what lets the
        /game/finish recompute ride on the evaluations /live/move
        already ran during the game instead of re-paying ~40 × 200 ms
        at the moment the user is waiting for the post-game summary.
        """
        if not self._started:
            raise RuntimeError("Engine pool not started")

        board = chess.Board(fen)
        # Computed eagerly so the flag list is identical on the
        # zero-score defence path below (engine returned no score), the
        # cache-hit path, and the happy paths — the LLM should see the
        # same board features regardless of where the score came from.
        tactical_flags = compute_tactical_flags(board)
        position_flags = compute_position_flags(board)

        if self.eval_cache is not None:
            cached = self.eval_cache.get(board=board, movetime_ms=movetime_ms)
            if cached is not None:
                return {
                    "evaluation": cached,
                    "tactical_flags": tactical_flags,
                    "position_flags": position_flags,
                }

        timeout_ms = queue_timeout_ms
        if timeout_ms is None:
            timeout_ms = self.settings.queue_timeout_ms
        if timeout_ms <= 0:
            timeout_ms = 1

        try:
            engine = self._engines.get(timeout=timeout_ms / 1000.0)
        except queue.Empty as exc:
            raise RuntimeError(f"Stockfish queue wait exceeded {timeout_ms}ms") from exc

        # Same healthy-flag contract as ``select_move``: a raise from
        # ``analyse`` (timeout, EngineError) is a strike against this
        # handle's hung-engine counter (P2 #6).
        healthy = False
        try:
            limit = chess.engine.Limit(time=movetime_ms / 1000.0)
            info = engine.analyse(board, limit)
            healthy = True
        finally:
            self._release_engine(engine, healthy=healthy)

        score = info.get("score") if isinstance(info, dict) else None
        if score is None:
            # Engine returned no score (shouldn't happen with a finite limit,
            # but defend against it).  Return a neutral cp eval so the caller
            # gets a usable shape; extract_engine_signal will tag this as
            # band="equal".  Deliberately NOT cached: this is a degraded
            # answer, and pinning it for hours would keep serving cp=0 for
            # a position the engine can genuinely score on the next call.
            return {
                "evaluation": {"type": "cp", "value": 0},
                "tactical_flags": tactical_flags,
                "position_flags": position_flags,
            }

        white_score = score.white()
        if white_score.is_mate():
            evaluation: dict = {"type": "mate", "value": int(white_score.mate() or 0)}
        else:
            cp = white_score.score(mate_score=10000)
            evaluation = {"type": "cp", "value": int(cp or 0)}

        if self.eval_cache is not None:
            self.eval_cache.set(board=board, movetime_ms=movetime_ms, evaluation=evaluation)
        return {
            "evaluation": evaluation,
            "tactical_flags": tactical_flags,
            "position_flags": position_flags,
        }

    def prewarm_cache(
        self,
        *,
        move_cache: FenMoveCache,
        fens: list[str],
        mode: str = "blitz",
        target_elo: int | None = None,
    ) -> int:
        warmed = 0
        movetime_ms = self.resolve_movetime_ms(mode, None)

        for fen in fens:
            fen = fen.strip()
            if not fen:
                continue

            try:
                cached_uci = move_cache.get(
                    fen=fen,
                    mode=mode,
                    movetime_ms=movetime_ms,
                    target_elo=target_elo,
                    line_key=None,
                )
                if cached_uci:
                    warmed += 1
                    continue

                move = self.select_move(
                    fen=fen,
                    mode=mode,
                    movetime_ms=movetime_ms,
                    target_elo=target_elo,
                )
                move_cache.set(
                    fen=fen,
                    mode=mode,
                    movetime_ms=movetime_ms,
                    target_elo=target_elo,
                    move_uci=move.uci(),
                    line_key=None,
                )
                warmed += 1
            except Exception:
                continue

        return warmed
