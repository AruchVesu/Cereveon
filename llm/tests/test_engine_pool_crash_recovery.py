"""
Engine pool crash-recovery tests — llm/tests/test_engine_pool_crash_recovery.py

Pin the contract that ``StockfishEnginePool`` does NOT return a dead
subprocess handle to the queue when the engine crashes mid-request.

Background
----------
Earlier revision of ``select_move``::

    try:
        engine = self._engines.get(timeout=...)
    except queue.Empty as exc:
        raise RuntimeError(...) from exc
    try:
        ...
        result = engine.play(resolved_board, limit)
        ...
        return result.move
    finally:
        self._engines.put(engine)   # <-- corpse goes right back in

If the Stockfish subprocess crashed inside ``engine.play(...)`` (SIGSEGV
on a malformed UCI command, OOM, kernel kill), the dead handle landed
straight back in the pool's queue.  The next acquirer pulled the corpse
and got a second-order failure; with pool_size=N this could cascade
across N requests before the queue cycled out.

The fix routes every release through ``_release_engine``, which uses a
cheap transport-liveness probe (``transport.is_closing()`` — no UCI
round-trip) to decide between "put back" and "replace".  Crashed
handles are dropped; a fresh engine is spawned to refill the slot;
the original exception still propagates to the caller so the request
fails loudly rather than silently retrying on a dead engine.

Stable test IDs (do NOT rename):
  CR_01  Healthy engine returns to pool after successful select_move
  CR_02  Engine that raised EngineTerminatedError is replaced, not pooled
  CR_03  Caller still sees the EngineTerminatedError (no silent swallow)
  CR_04  Engine whose transport is closing post-call is replaced
  CR_05  Recoverable error keeps the original engine in the pool
  CR_06  Pool size is preserved after a crash + respawn
  CR_07  Failed respawn forfeits the slot rather than blocking forever
  CR_08  Crash inside _apply_runtime_options is also handled
"""

from __future__ import annotations

import unittest

import chess
import chess.engine

from llm.seca.engines.stockfish.pool import EnginePoolSettings, StockfishEnginePool


_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class _FakeTransport:
    """Stand-in for ``asyncio.SubprocessTransport``.  The real pool
    only consults ``is_closing()`` on the transport — we wire that to
    a mutable flag so a test can flip the engine from alive to dead
    without touching the rest of the surface."""

    def __init__(self, *, closing: bool = False) -> None:
        self._closing = closing

    def is_closing(self) -> bool:
        return self._closing

    def mark_closed(self) -> None:
        self._closing = True


class _FakeEngine:
    """Minimal SimpleEngine stand-in.  Provides the exact attributes
    ``select_move`` and ``_release_engine`` touch and nothing else."""

    def __init__(
        self,
        *,
        play_raises: BaseException | None = None,
        configure_raises: BaseException | None = None,
        transport_closes_after_play: bool = False,
        transport_already_closing: bool = False,
    ) -> None:
        self.transport = _FakeTransport(closing=transport_already_closing)
        self._play_raises = play_raises
        self._configure_raises = configure_raises
        self._transport_closes_after_play = transport_closes_after_play
        self.play_call_count = 0
        self.configure_call_count = 0
        self.quit_called = False

    def play(self, board: chess.Board, limit: chess.engine.Limit, **_):
        self.play_call_count += 1
        if self._play_raises is not None:
            if self._transport_closes_after_play:
                self.transport.mark_closed()
            raise self._play_raises
        if self._transport_closes_after_play:
            # Subprocess returned a move and then died (rare but real:
            # OOM-killer fires after the response is on the wire).
            self.transport.mark_closed()
        move = next(iter(board.legal_moves), None)
        if move is None:
            raise RuntimeError("No legal moves available in fake engine")
        return chess.engine.PlayResult(move=move, ponder=None)

    def configure(self, options):
        self.configure_call_count += 1
        if self._configure_raises is not None:
            raise self._configure_raises

    def quit(self):
        self.quit_called = True


def _settings(pool_size: int = 1) -> EnginePoolSettings:
    return EnginePoolSettings(
        stockfish_path="/dev/null",  # never opened — pool is hand-populated
        pool_size=pool_size,
        queue_timeout_ms=50,
    )


def _pool_with(*, initial_engine, spawn_returns=None, spawn_raises=None) -> StockfishEnginePool:
    """Build a pool whose queue starts with *initial_engine* and whose
    ``_spawn_engine`` returns the given replacement (or raises).

    Bypasses ``startup()`` to avoid touching the real Stockfish binary.
    """
    pool = StockfishEnginePool(_settings(pool_size=1))
    pool._started = True
    pool._engines.put(initial_engine)

    if spawn_raises is not None:
        def _failing_spawn():
            raise spawn_raises
        pool._spawn_engine = _failing_spawn  # type: ignore[assignment]
    elif spawn_returns is not None:
        replacements = list(spawn_returns) if isinstance(spawn_returns, list) else [spawn_returns]
        def _spawn_next():
            return replacements.pop(0) if replacements else _FakeEngine()
        pool._spawn_engine = _spawn_next  # type: ignore[assignment]

    return pool


# ---------------------------------------------------------------------------
# CR_01 — healthy round-trip
# ---------------------------------------------------------------------------


class TestHealthyEngineReturnsToPool(unittest.TestCase):
    def test_cr_01_healthy_engine_returns_to_pool(self):
        engine = _FakeEngine()
        pool = _pool_with(initial_engine=engine)

        move = pool.select_move(fen=_STARTING_FEN)

        self.assertIsInstance(move, chess.Move)
        self.assertEqual(pool.qsize(), 1, "healthy engine must return to pool")
        # Confirm it's the SAME engine, not a respawn.
        self.assertIs(pool._engines.queue[0], engine)
        self.assertFalse(engine.quit_called, "healthy engine must not be quit-on-release")


# ---------------------------------------------------------------------------
# CR_02, CR_03 — EngineTerminatedError triggers replacement + propagates
# ---------------------------------------------------------------------------


class TestEngineTerminatedErrorTriggersReplace(unittest.TestCase):
    def test_cr_02_dead_engine_replaced_not_pooled(self):
        dead = _FakeEngine(
            play_raises=chess.engine.EngineTerminatedError("subprocess died"),
            transport_closes_after_play=True,
        )
        replacement = _FakeEngine()
        pool = _pool_with(initial_engine=dead, spawn_returns=replacement)

        with self.assertRaises(chess.engine.EngineTerminatedError):
            pool.select_move(fen=_STARTING_FEN)

        self.assertEqual(pool.qsize(), 1, "pool size must be preserved after crash")
        pooled = pool._engines.queue[0]
        self.assertIs(pooled, replacement, "replacement must take the slot")
        self.assertIsNot(pooled, dead, "dead handle must not be pooled")

    def test_cr_03_caller_sees_engine_terminated_error(self):
        # Surfaces explicitly so a future "swallow EngineTerminatedError
        # and return a fallback move" refactor regresses CI.  The caller
        # MUST know the request failed; downstream retry / monitoring
        # depends on that signal.
        dead = _FakeEngine(
            play_raises=chess.engine.EngineTerminatedError("crash"),
            transport_closes_after_play=True,
        )
        pool = _pool_with(initial_engine=dead, spawn_returns=_FakeEngine())

        with self.assertRaises(chess.engine.EngineTerminatedError) as cm:
            pool.select_move(fen=_STARTING_FEN)
        self.assertIn("crash", str(cm.exception))


# ---------------------------------------------------------------------------
# CR_04 — engine returned a move but transport is closing → replace anyway
# ---------------------------------------------------------------------------


class TestClosingTransportTriggersReplace(unittest.TestCase):
    def test_cr_04_closed_transport_post_call_replaced(self):
        # Real-world case: Stockfish returns a move but the OOM killer
        # fires immediately after.  ``play()`` does not raise, but the
        # transport is now in a closing state.  The next acquirer would
        # discover this only by trying another UCI command.  The
        # liveness probe in ``_release_engine`` catches it now.
        engine = _FakeEngine(transport_closes_after_play=True)
        replacement = _FakeEngine()
        pool = _pool_with(initial_engine=engine, spawn_returns=replacement)

        move = pool.select_move(fen=_STARTING_FEN)
        self.assertIsInstance(move, chess.Move, "the move from the dying engine is still valid")

        self.assertEqual(pool.qsize(), 1)
        self.assertIs(
            pool._engines.queue[0],
            replacement,
            "engine with closing transport must be replaced even if play() succeeded",
        )


# ---------------------------------------------------------------------------
# CR_05 — non-crash error keeps the engine in the pool
# ---------------------------------------------------------------------------


class TestRecoverableErrorKeepsEngine(unittest.TestCase):
    def test_cr_05_recoverable_error_engine_stays(self):
        # A plain ``RuntimeError`` (e.g. "Stockfish returned no move"
        # from the fallback branch) is not a process-death signal.
        # The engine is still alive; we must NOT pay the cost of a
        # respawn for every recoverable error.
        engine = _FakeEngine(play_raises=RuntimeError("no legal moves"))
        # spawn_returns is intentionally omitted — recovery should NOT
        # attempt a respawn here, so any spawn call would be a bug.
        pool = StockfishEnginePool(_settings(pool_size=1))
        pool._started = True
        pool._engines.put(engine)

        def _spawn_should_not_be_called():
            self.fail("recoverable error must not trigger respawn")
        pool._spawn_engine = _spawn_should_not_be_called  # type: ignore[assignment]

        with self.assertRaises(RuntimeError):
            pool.select_move(fen=_STARTING_FEN)

        self.assertEqual(pool.qsize(), 1)
        self.assertIs(pool._engines.queue[0], engine)


# ---------------------------------------------------------------------------
# CR_06 — crash + respawn keeps pool size constant across two calls
# ---------------------------------------------------------------------------


class TestPoolSizePreservedAcrossCrash(unittest.TestCase):
    def test_cr_06_size_preserved_two_calls_one_crash(self):
        dead = _FakeEngine(
            play_raises=chess.engine.EngineTerminatedError("died"),
            transport_closes_after_play=True,
        )
        replacement = _FakeEngine()
        pool = _pool_with(initial_engine=dead, spawn_returns=replacement)

        with self.assertRaises(chess.engine.EngineTerminatedError):
            pool.select_move(fen=_STARTING_FEN)
        self.assertEqual(pool.qsize(), 1, "pool size after crash + respawn")

        # Next call must succeed against the replacement.
        move = pool.select_move(fen=_STARTING_FEN)
        self.assertIsInstance(move, chess.Move)
        self.assertEqual(pool.qsize(), 1, "pool size after second successful call")


# ---------------------------------------------------------------------------
# CR_07 — respawn failure forfeits the slot, no deadlock
# ---------------------------------------------------------------------------


class TestRespawnFailureForfeitsSlot(unittest.TestCase):
    def test_cr_07_failed_respawn_forfeits_slot(self):
        # If ``_spawn_engine`` itself raises (binary missing, FD
        # exhaustion), the slot is forfeited rather than blocking
        # forever waiting for a healthy spawn.  Operators see a
        # WARNING in the log and can restart; the alternative is a
        # deadlocked pool where every request times out.
        dead = _FakeEngine(
            play_raises=chess.engine.EngineTerminatedError("died"),
            transport_closes_after_play=True,
        )
        pool = _pool_with(
            initial_engine=dead,
            spawn_raises=OSError("FileNotFoundError: stockfish"),
        )

        with self.assertRaises(chess.engine.EngineTerminatedError):
            pool.select_move(fen=_STARTING_FEN)

        # Slot is gone but the pool is still operational (no deadlock,
        # no dangling reference).  qsize=0 is the expected state; the
        # next select_move will time out cleanly.
        self.assertEqual(pool.qsize(), 0)


# ---------------------------------------------------------------------------
# CR_08 — crash inside configure() (UCI option set) is detected
# ---------------------------------------------------------------------------


class TestConfigureCrashHandled(unittest.TestCase):
    def test_cr_08_configure_terminated_error_replaces_engine(self):
        # ``_apply_runtime_options`` calls ``engine.configure(...)``
        # which can raise EngineTerminatedError if the subprocess died
        # between previous use and this call.  The original code
        # caught EngineError generically and silently fell through —
        # that swallowed the death signal and the next ``play`` ran
        # against a dead engine.  Pin the explicit re-raise.
        dead = _FakeEngine(
            configure_raises=chess.engine.EngineTerminatedError("died on configure"),
            transport_already_closing=True,
        )
        replacement = _FakeEngine()
        pool = _pool_with(initial_engine=dead, spawn_returns=replacement)

        with self.assertRaises(chess.engine.EngineTerminatedError):
            pool.select_move(fen=_STARTING_FEN, target_elo=1500)

        self.assertEqual(pool.qsize(), 1)
        self.assertIs(pool._engines.queue[0], replacement)


if __name__ == "__main__":
    unittest.main(verbosity=2)
