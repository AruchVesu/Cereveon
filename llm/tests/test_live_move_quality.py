"""Tests for server._live_move_quality — the /live/move move-quality wiring.

Verifies the before->after eval swing is graded correctly, that the integrity
check rejects a pre-move FEN that doesn't reach the post-move FEN, and that the
helper degrades to None (move quality "unknown") when the engine pool is absent.
The pure classification is covered separately in test_move_quality.py.
"""

import asyncio

import chess

import llm.server as server


class _FakePool:
    """Returns canned evaluate_position results keyed by FEN."""

    def __init__(self, eval_by_fen: dict):
        self._eval_by_fen = eval_by_fen

    def evaluate_position(self, *, fen, movetime_ms=200, queue_timeout_ms=None):
        return self._eval_by_fen[fen]


_START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _after(fen: str, uci: str) -> str:
    board = chess.Board(fen)
    board.push(chess.Move.from_uci(uci))
    return board.fen()


def test_good_move_graded_best(monkeypatch):
    after = _after(_START, "e2e4")
    monkeypatch.setattr(
        server, "engine_pool", _FakePool({_START: {"evaluation": {"type": "cp", "value": 30}}})
    )
    quality = asyncio.run(
        server._live_move_quality(
            _START, after, "e2e4", {"evaluation": {"type": "cp", "value": 25}}
        )
    )
    assert quality == "best"  # player-perspective loss 5


def test_bad_move_graded_blunder(monkeypatch):
    after = _after(_START, "e2e4")
    monkeypatch.setattr(
        server, "engine_pool", _FakePool({_START: {"evaluation": {"type": "cp", "value": 50}}})
    )
    quality = asyncio.run(
        server._live_move_quality(
            _START, after, "e2e4", {"evaluation": {"type": "cp", "value": -400}}
        )
    )
    assert quality == "blunder"  # loss 450


def test_integrity_mismatch_returns_none(monkeypatch):
    # fen_before + uci ("e2e4") does NOT reach the claimed post-move FEN (d2d4).
    wrong_after = _after(_START, "d2d4")
    monkeypatch.setattr(
        server, "engine_pool", _FakePool({_START: {"evaluation": {"type": "cp", "value": 30}}})
    )
    quality = asyncio.run(
        server._live_move_quality(
            _START, wrong_after, "e2e4", {"evaluation": {"type": "cp", "value": 25}}
        )
    )
    assert quality is None


def test_no_engine_pool_returns_none(monkeypatch):
    after = _after(_START, "e2e4")
    monkeypatch.setattr(server, "engine_pool", None)
    quality = asyncio.run(
        server._live_move_quality(
            _START, after, "e2e4", {"evaluation": {"type": "cp", "value": 25}}
        )
    )
    assert quality is None


def test_black_mover_perspective(monkeypatch):
    # After 1.e4, Black to move; Black plays a bad move dropping the eval for Black.
    fen_before = _after(_START, "e2e4")  # black to move
    after = _after(fen_before, "d7d5")
    # White-relative: before +20 (slight White edge), after +500 (White much better)
    # => from Black's perspective: before -20, after -500 => loss 480 => blunder.
    monkeypatch.setattr(
        server,
        "engine_pool",
        _FakePool({fen_before: {"evaluation": {"type": "cp", "value": 20}}}),
    )
    quality = asyncio.run(
        server._live_move_quality(
            fen_before, after, "d7d5", {"evaluation": {"type": "cp", "value": 500}}
        )
    )
    assert quality == "blunder"
