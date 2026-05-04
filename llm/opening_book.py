from __future__ import annotations

import os
from pathlib import Path
from threading import Lock

import chess
import chess.polyglot

try:
    from .position_input import build_board
except ImportError:
    from position_input import build_board

MODULE_DIR = Path(__file__).resolve().parent
ROOT_BOOK_PATH = MODULE_DIR.parent / "books" / "performance.bin"
LEGACY_BOOK_PATH = MODULE_DIR / "books" / "performance.bin"


def _default_book_path() -> str:
    if ROOT_BOOK_PATH.exists():
        return str(ROOT_BOOK_PATH)
    return str(LEGACY_BOOK_PATH)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class OpeningBook:
    def __init__(self, path: str | None = None):
        configured_path = path or os.getenv("OPENING_BOOK_PATH") or _default_book_path()
        self.path = os.path.abspath(configured_path) if configured_path else _default_book_path()
        self.score = int(os.getenv("OPENING_BOOK_SCORE", "20"))
        self.enabled = _env_bool("OPENING_BOOK_ENABLED", True)
        self.selection = os.getenv("OPENING_BOOK_SELECTION", "best").strip().lower()
        self._lock = Lock()
        self._reader: chess.polyglot.MemoryMappedReader | None = None

        if not self.enabled:
            return
        if not os.path.exists(self.path):
            return

        try:
            self._reader = chess.polyglot.open_reader(self.path)
        except Exception:
            self._reader = None

    @property
    def available(self) -> bool:
        return self._reader is not None

    def get_move(
        self,
        fen: str | None = None,
        moves: list[str] | None = None,
    ) -> dict | None:
        if self._reader is None:
            return None

        try:
            board = build_board(fen=fen, moves=moves)
        except ValueError:
            return None

        try:
            with self._lock:
                if self.selection == "weighted":
                    entry = self._reader.weighted_choice(board)
                else:
                    entry = self._reader.find(board)
        except IndexError:
            return None
        except Exception:
            return None

        return {
            "best_move": entry.move.uci(),
            "score": self.score,
            "source": "book",
        }

    def lookup(
        self,
        *,
        fen: str | None = None,
        moves: list[str] | None = None,
    ) -> dict | None:
        return self.get_move(fen=fen, moves=moves)

    def close(self) -> None:
        if self._reader is None:
            return
        try:
            self._reader.close()
        except Exception:
            pass
