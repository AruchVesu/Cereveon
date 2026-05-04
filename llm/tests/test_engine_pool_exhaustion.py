"""
Engine pool exhaustion tests.

Verifies that StockfishEnginePool raises a graceful RuntimeError — rather than
blocking indefinitely or crashing — when all pool slots are in use and a new
select_move() call times out waiting for a free engine.

No real Stockfish process is spawned. The pool's internal queue is populated
with fake engine objects so the queue-drain logic can be exercised in CI.

Pinned invariants
-----------------
 1. POOL_NOT_STARTED:         select_move() before startup() raises RuntimeError.
 2. QUEUE_EMPTY_TIMEOUT:      select_move() with empty queue raises RuntimeError
                               containing "queue wait exceeded".
 3. ERROR_MESSAGE_DESCRIPTIVE: RuntimeError message includes the timeout value.
 4. FAST_TIMEOUT:              timeout_ms=1 raises within a wall-clock second.
 5. AFTER_RELEASE_SUCCEEDS:   After the engine is returned to the pool a second
                               select_move() call completes without error.
 6. QUEUE_SIZE_ZERO:           qsize() returns 0 when all slots are borrowed.
 7. QUEUE_SIZE_RESTORED:       qsize() returns 1 after the fake engine is returned.
"""

from __future__ import annotations

import queue
import threading
import time

import chess
import chess.engine
import pytest

from llm.seca.engines.stockfish.pool import EnginePoolSettings, StockfishEnginePool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_settings(pool_size: int = 1, queue_timeout_ms: int = 50) -> EnginePoolSettings:
    return EnginePoolSettings(
        stockfish_path="/dev/null",  # never opened — engine is injected directly
        pool_size=pool_size,
        queue_timeout_ms=queue_timeout_ms,
    )


class _FakeEngine:
    """Minimal stand-in; pool only calls engine.play() which we override."""

    def play(self, board, limit, **kwargs):
        move = next(iter(board.legal_moves), None)
        if move is None:
            raise RuntimeError("No legal moves")
        return chess.engine.PlayResult(move=move, ponder=None)

    def configure(self, options):
        pass

    def quit(self):
        pass


def _pool_with_fake_engine(pool_size: int = 1, queue_timeout_ms: int = 50) -> StockfishEnginePool:
    """Return a pool whose queue is pre-populated with a single fake engine."""
    pool = StockfishEnginePool(_minimal_settings(pool_size, queue_timeout_ms))
    # Bypass startup() (which would try to spawn real Stockfish).
    pool._started = True
    for _ in range(pool_size):
        pool._engines.put(_FakeEngine())
    return pool


_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


# ---------------------------------------------------------------------------
# 1. POOL_NOT_STARTED
# ---------------------------------------------------------------------------


def test_select_move_raises_when_pool_not_started():
    """select_move() before startup() raises RuntimeError."""
    pool = StockfishEnginePool(_minimal_settings())
    # _started is False by default — do NOT call startup()
    with pytest.raises(RuntimeError, match="not started"):
        pool.select_move(fen=_STARTING_FEN)


# ---------------------------------------------------------------------------
# 2. QUEUE_EMPTY_TIMEOUT
# ---------------------------------------------------------------------------


def test_select_move_raises_on_empty_queue():
    """select_move() with an empty queue raises RuntimeError about timeout."""
    pool = StockfishEnginePool(_minimal_settings(queue_timeout_ms=1))
    pool._started = True
    # Queue is empty — no fake engine injected
    with pytest.raises(RuntimeError, match="queue wait exceeded"):
        pool.select_move(fen=_STARTING_FEN, queue_timeout_ms=1)


# ---------------------------------------------------------------------------
# 3. ERROR_MESSAGE_DESCRIPTIVE
# ---------------------------------------------------------------------------


def test_exhaustion_error_includes_timeout_ms():
    """RuntimeError message includes the timeout value in milliseconds."""
    pool = StockfishEnginePool(_minimal_settings(queue_timeout_ms=5))
    pool._started = True
    with pytest.raises(RuntimeError) as exc_info:
        pool.select_move(fen=_STARTING_FEN, queue_timeout_ms=5)
    assert "5" in str(exc_info.value), (
        f"Expected timeout value '5' in error message: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# 4. FAST_TIMEOUT
# ---------------------------------------------------------------------------


def test_exhaustion_raises_within_one_second():
    """timeout_ms=1 raises within a wall-clock second (no indefinite blocking)."""
    pool = StockfishEnginePool(_minimal_settings(queue_timeout_ms=1))
    pool._started = True
    start = time.monotonic()
    with pytest.raises(RuntimeError):
        pool.select_move(fen=_STARTING_FEN, queue_timeout_ms=1)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, (
        f"Pool exhaustion should raise within 1 s but took {elapsed:.3f} s"
    )


# ---------------------------------------------------------------------------
# 5. AFTER_RELEASE_SUCCEEDS
# ---------------------------------------------------------------------------


def test_second_call_succeeds_after_engine_released():
    """After the fake engine is returned to the queue a new call succeeds."""
    pool = _pool_with_fake_engine(pool_size=1, queue_timeout_ms=50)
    board = chess.Board(_STARTING_FEN)

    # First call — borrows the engine, plays, and returns it automatically.
    move1 = pool.select_move(fen=_STARTING_FEN, board=board)
    assert isinstance(move1, chess.Move)

    # Second call — the engine is back in the pool.
    board2 = chess.Board(_STARTING_FEN)
    move2 = pool.select_move(fen=_STARTING_FEN, board=board2)
    assert isinstance(move2, chess.Move)


# ---------------------------------------------------------------------------
# 6. QUEUE_SIZE_ZERO
# ---------------------------------------------------------------------------


def test_qsize_is_zero_while_engine_borrowed():
    """qsize() returns 0 when the single engine slot is in use by another thread."""
    pool = _pool_with_fake_engine(pool_size=1, queue_timeout_ms=200)

    borrowed_event = threading.Event()
    release_event = threading.Event()
    result = {}

    def _borrow():
        # Borrow the engine from the queue directly, mimicking an in-progress call.
        engine = pool._engines.get(timeout=1.0)
        borrowed_event.set()
        release_event.wait(timeout=2.0)
        pool._engines.put(engine)

    t = threading.Thread(target=_borrow)
    t.start()
    borrowed_event.wait(timeout=2.0)

    assert pool.qsize() == 0, (
        f"Expected qsize=0 while engine is borrowed, got {pool.qsize()}"
    )

    release_event.set()
    t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# 7. QUEUE_SIZE_RESTORED
# ---------------------------------------------------------------------------


def test_qsize_restored_after_select_move():
    """qsize() returns 1 after select_move() finishes (engine returned to pool)."""
    pool = _pool_with_fake_engine(pool_size=1, queue_timeout_ms=200)
    board = chess.Board(_STARTING_FEN)
    pool.select_move(fen=_STARTING_FEN, board=board)
    assert pool.qsize() == 1, (
        f"Expected qsize=1 after select_move(), got {pool.qsize()}"
    )
