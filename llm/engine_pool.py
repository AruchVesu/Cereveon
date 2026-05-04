from __future__ import annotations

import asyncio
import os
import shutil
from contextlib import asynccontextmanager
from typing import List, Optional, Tuple

import chess.engine

STOCKFISH_PATH = os.getenv("STOCKFISH_PATH", "/usr/games/stockfish")


def resolve_stockfish_path(path: str | None = None) -> str | None:
    candidate = path or STOCKFISH_PATH
    if candidate and os.path.exists(candidate):
        return candidate

    by_name = shutil.which(candidate or "stockfish")
    if by_name:
        return by_name

    fallback = shutil.which("stockfish")
    if fallback:
        return fallback
    return None


class EnginePool:
    def __init__(self, size: int = 4, stockfish_path: str | None = None):
        self.size = max(1, size)
        self.stockfish_path = stockfish_path or STOCKFISH_PATH
        self._queue: asyncio.Queue[chess.engine.UciProtocol] = asyncio.Queue(maxsize=self.size)
        self._engines: List[Tuple[asyncio.SubprocessTransport, chess.engine.UciProtocol]] = []
        self._started = False
        self._threads = max(1, int(os.getenv("ENGINE_THREADS", "1")))
        self._hash_mb = max(1, int(os.getenv("ENGINE_HASH_MB", "16")))
        self._startup_delay_ms = max(0, int(os.getenv("ENGINE_STARTUP_DELAY_MS", "5")))

    async def start(self):
        if self._started:
            return

        command = resolve_stockfish_path(self.stockfish_path)
        if command is None:
            raise FileNotFoundError(
                f"Stockfish binary not found (requested: {self.stockfish_path})"
            )

        first_error: Exception | None = None
        for _ in range(self.size):
            transport = None
            engine = None
            try:
                transport, engine = await chess.engine.popen_uci(command)
                try:
                    await engine.configure({"Threads": self._threads, "Hash": self._hash_mb})
                except Exception:
                    # Some engine builds may not expose one or both options.
                    pass
                self._engines.append((transport, engine))
                await self._queue.put(engine)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                if engine is not None:
                    try:
                        await engine.quit()
                    except Exception:
                        pass
                if transport is not None:
                    try:
                        transport.close()
                    except Exception:
                        pass
            if self._startup_delay_ms:
                await asyncio.sleep(self._startup_delay_ms / 1000.0)

        if not self._engines:
            detail = ""
            if first_error is not None:
                detail = f": {type(first_error).__name__}: {first_error!r}"
            raise RuntimeError(f"Failed to start any Stockfish engine process{detail}")
        self._started = True

    async def stop(self):
        if not self._started:
            return
        self._started = False  # prevent new acquires before engines are torn down

        for transport, engine in self._engines:
            try:
                await engine.quit()
            except Exception:
                pass
            try:
                transport.close()
            except Exception:
                pass

        self._engines.clear()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def acquire(self) -> chess.engine.UciProtocol:
        if not self._started:
            raise RuntimeError("EnginePool is not started")
        return await self._queue.get()

    def acquire_nowait(self) -> chess.engine.UciProtocol | None:
        if not self._started:
            raise RuntimeError("EnginePool is not started")
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def try_acquire(self) -> chess.engine.UciProtocol | None:
        return self.acquire_nowait()

    async def release(self, engine: chess.engine.UciProtocol):
        if self._started:
            await self._queue.put(engine)

    @asynccontextmanager
    async def get_engine(self):
        engine = await self.acquire()
        try:
            yield engine
        finally:
            await self.release(engine)

    @property
    def available(self) -> int:
        return self._queue.qsize()

    @property
    def capacity(self) -> int:
        return len(self._engines)
