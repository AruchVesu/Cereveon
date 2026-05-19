"""Tests for ``llm.seca.analysis.pgn_accuracy``.

Closes the trust-gap regression coverage for /game/finish:
- compute_accuracy_from_pgn produces engine-truth accuracy + weakness
  values from a submitted PGN, independent of any client claim.
- A modded client that inflates ``accuracy`` toward 1.0 cannot bypass
  the recompute — the server's value comes from engine evaluation, not
  the request field.
- The events router's _resolve_authoritative_accuracy wrapper:
  * uses engine recompute when ``app.state.engine_pool`` is present.
  * falls back to client values when the pool is missing.
  * emits an ACC_DIVERGENCE warning at ≥ 20 percentage-point delta.

Stable test IDs (do NOT rename):
  PGNACC_UNIT_*   compute_accuracy_from_pgn unit tests
  PGNACC_RES_*    _resolve_authoritative_accuracy wrapper tests
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Iterable

import chess
import chess.pgn
import pytest

from llm.seca.analysis.pgn_accuracy import (
    AccuracyAnalysis,
    compute_accuracy_from_pgn,
)
from llm.seca.events.router import _resolve_authoritative_accuracy


# ---------------------------------------------------------------------------
# Fake engine pool
# ---------------------------------------------------------------------------


@dataclass
class _FakeEvalPool:
    """Drop-in stand-in for ``StockfishEnginePool`` exposing the single
    ``evaluate_position`` method the accuracy recompute calls.

    ``cp_by_fen`` maps a starting-fragment of FEN (board layout only,
    no side / castling / move-counters) to a deterministic centipawn
    score from White's POV.  Positions not in the map default to 0
    (equal).  ``raise_on_call`` lets a test simulate engine
    unavailability mid-recompute.
    """

    cp_by_fen: dict[str, int]
    raise_on_call: type[Exception] | None = None
    call_count: int = 0

    def evaluate_position(
        self,
        *,
        fen: str,
        movetime_ms: int,
        queue_timeout_ms: int | None = None,
    ) -> dict:
        # ``queue_timeout_ms`` is accepted (and ignored) so this fake
        # matches the production pool's signature after R1.
        self.call_count += 1
        if self.raise_on_call is not None:
            raise self.raise_on_call("simulated engine failure")
        board_fen = fen.split(" ")[0]  # piece placement only
        cp = self.cp_by_fen.get(board_fen, 0)
        return {"evaluation": {"type": "cp", "value": cp}}


def _pgn(moves_san: Iterable[str], *, result: str = "1-0") -> str:
    moves = " ".join(moves_san)
    return f"""[Event "Test"]
[Result "{result}"]

{moves} {result}
"""


# ---------------------------------------------------------------------------
# PGNACC_UNIT — compute_accuracy_from_pgn
# ---------------------------------------------------------------------------


class TestPgnAccuracyHappyPath:
    """PGNACC_UNIT_HAPPY: clean games produce high accuracy + no
    blunders; blunder-heavy games produce low accuracy."""

    def test_clean_game_high_accuracy(self):
        """PGNACC_UNIT_HAPPY_CLEAN: equal-eval moves throughout → ACPL=0
        → accuracy=1.0, zero blunders."""
        pgn = _pgn(["e4", "e5", "Nf3", "Nc6"], result="1-0")
        # Every position evaluates to 0 — no centipawn loss anywhere.
        pool = _FakeEvalPool(cp_by_fen={})
        analysis = compute_accuracy_from_pgn(
            pgn_text=pgn,
            engine_pool=pool,
            result="win",
        )
        assert analysis.source == "engine"
        assert analysis.accuracy == pytest.approx(1.0)
        assert analysis.blunder_count == 0
        assert analysis.mistake_count == 0
        assert analysis.inaccuracy_count == 0
        # Player won, PGN result was 1-0 → player is White.
        assert analysis.player_color == chess.WHITE
        # 4 plies played; White moved twice (plies 1 and 3).
        assert analysis.moves_analyzed == 2

    def test_blunder_for_player_drops_accuracy(self):
        """PGNACC_UNIT_HAPPY_BLUNDER: a 400cp swing on the player's move
        is classified as a blunder; accuracy drops accordingly.

        Setup: Black plays a blundering e5 (modeled as a 400cp swing
        in the fake eval), and the game ends 1-0 (White wins).  Player
        reported "loss" → inference says player is Black.
        """
        pgn = _pgn(["e4", "e5", "Nf3"], result="1-0")
        pool = _FakeEvalPool(
            cp_by_fen={
                _board_after(["e4"]).split(" ")[0]: 0,
                _board_after(["e4", "e5"]).split(" ")[0]: 400,
                _board_after(["e4", "e5", "Nf3"]).split(" ")[0]: 400,
            }
        )
        analysis = compute_accuracy_from_pgn(
            pgn_text=pgn,
            engine_pool=pool,
            result="loss",
        )
        assert analysis.source == "engine"
        assert analysis.player_color == chess.BLACK
        # Black has one move (e5) that's a blunder (eval going UP for
        # White = bad for Black, 400 ≥ 300 threshold).
        assert analysis.blunder_count == 1
        assert analysis.accuracy < 0.5

    def test_player_color_inference_white_loss(self):
        """PGNACC_UNIT_HAPPY_COLOR_LOSS_WHITE: PGN result 0-1 + reported
        loss ⇒ player was White."""
        pgn = _pgn(["e4"], result="0-1")
        pool = _FakeEvalPool(cp_by_fen={})
        analysis = compute_accuracy_from_pgn(
            pgn_text=pgn,
            engine_pool=pool,
            result="loss",
        )
        assert analysis.player_color == chess.WHITE

    def test_player_color_inference_black_win(self):
        """PGNACC_UNIT_HAPPY_COLOR_WIN_BLACK: PGN result 0-1 + reported
        win ⇒ player was Black."""
        pgn = _pgn(["e4", "e5"], result="0-1")
        pool = _FakeEvalPool(cp_by_fen={})
        analysis = compute_accuracy_from_pgn(
            pgn_text=pgn,
            engine_pool=pool,
            result="win",
        )
        assert analysis.player_color == chess.BLACK


class TestPgnAccuracyEdgeCases:
    """PGNACC_UNIT_EDGE: malformed inputs, empty games, mate values."""

    def test_malformed_pgn_returns_fallback(self):
        """PGNACC_UNIT_EDGE_MALFORMED: junk PGN string returns a
        fallback analysis (source='fallback', moves_analyzed=0).

        Pinned because ``chess.pgn.read_game`` auto-fills default
        headers and returns an empty-mainline game rather than None
        for non-PGN input; the wrapper in events/router treats the
        zero-moves-analysed outcome as a fallback signal and reverts
        to client-supplied values.
        """
        pool = _FakeEvalPool(cp_by_fen={})
        analysis = compute_accuracy_from_pgn(
            pgn_text="not a real pgn",
            engine_pool=pool,
            result="win",
        )
        assert analysis.source == "fallback"
        assert analysis.moves_analyzed == 0
        # Fake pool never gets called because there are no moves.
        assert pool.call_count == 0

    def test_empty_pgn_returns_fallback(self):
        """PGNACC_UNIT_EDGE_EMPTY: PGN with no moves → fallback analysis."""
        pgn = '[Event "Test"]\n[Result "*"]\n\n*\n'
        pool = _FakeEvalPool(cp_by_fen={})
        analysis = compute_accuracy_from_pgn(
            pgn_text=pgn,
            engine_pool=pool,
            result="draw",
        )
        # No moves played → fallback shape with neutral accuracy.
        assert analysis.source == "fallback"
        assert analysis.moves_analyzed == 0
        assert analysis.accuracy == 0.5
        assert analysis.weaknesses == {}

    def test_engine_failure_propagates(self):
        """PGNACC_UNIT_EDGE_ENGINE_DOWN: RuntimeError from the pool
        propagates so the caller's fallback path can handle it."""
        pgn = _pgn(["e4", "e5"], result="*")
        pool = _FakeEvalPool(cp_by_fen={}, raise_on_call=RuntimeError)
        with pytest.raises(RuntimeError, match="simulated"):
            compute_accuracy_from_pgn(
                pgn_text=pgn,
                engine_pool=pool,
                result="draw",
            )

    def test_max_plies_caps_analysis(self):
        """PGNACC_UNIT_EDGE_MAX_PLIES: max_plies cap is honored — engine
        is called at most max_plies + 1 times (one initial + one per ply)."""
        moves = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6"]
        pgn = _pgn(moves, result="*")
        pool = _FakeEvalPool(cp_by_fen={})
        compute_accuracy_from_pgn(
            pgn_text=pgn,
            engine_pool=pool,
            result="draw",
            max_plies=4,
        )
        # 4 plies + 1 initial eval = 5 calls.  The cap stops further
        # iteration before exceeding the budget.
        assert pool.call_count == 5

    def test_invalid_move_in_pgn_returns_partial_analysis(self):
        """PGNACC_UNIT_EDGE_INVALID_MOVE: a PGN with an unrecognized SAN
        token has the bad move silently dropped by python-chess; the
        function returns analysis based on the valid moves only.

        Pinned because the validator in events/router rejects empty
        mainlines at the Pydantic boundary (no-moves-found check), so
        this function only sees malformed-but-non-empty PGNs via
        direct unit-test invocation or callers that bypass the
        validator.  The wrapper in events/router handles the
        zero-player-moves edge via HTTPException(422); see
        TestResolveAuthoritativeAccuracy.test_zero_player_moves_raises_422.

        The previous version of this test hedged with try/except — that
        accepted any outcome and wouldn't catch a behavior regression.
        Empirically (python-chess 1.999): ``1. e4 e9 *`` parses to
        mainline=[e4]; the illegal ``e9`` is silently dropped.
        """
        bad_pgn = '[Event "Test"]\n[Result "*"]\n\n1. e4 e9 *\n'
        pool = _FakeEvalPool(cp_by_fen={})
        analysis = compute_accuracy_from_pgn(
            pgn_text=bad_pgn,
            engine_pool=pool,
            result="draw",
        )
        assert analysis.source == "engine"
        assert analysis.moves_analyzed == 1  # the e4 ply
        assert analysis.player_color == chess.WHITE  # draw → default White


class TestPgnAccuracyQueueTimeout:
    """PGNACC_UNIT_TIMEOUT: per-ply engine acquire passes a generous
    queue_timeout_ms — not the pool's snappy /live/move default."""

    def test_evaluate_passes_higher_queue_timeout(self):
        """PGNACC_UNIT_TIMEOUT_PASSTHROUGH: R1 regression — concurrent
        /live/move pressure (and the pool's 50ms default) must not
        demote the recompute to a spurious fallback.  ``_evaluate_cp``
        passes ``_RECOMPUTE_QUEUE_TIMEOUT_MS`` explicitly so the
        per-acquire budget is decoupled from the live-move tuning.
        """
        from llm.seca.analysis.pgn_accuracy import _RECOMPUTE_QUEUE_TIMEOUT_MS

        assert _RECOMPUTE_QUEUE_TIMEOUT_MS >= 500, (
            "recompute queue timeout must accommodate concurrent /live/move "
            "load; 50ms (live default) is too tight for a 40-acquire batch."
        )

        captured: list[int | None] = []

        class CapturingPool:
            def evaluate_position(
                self,
                *,
                fen: str,
                movetime_ms: int,
                queue_timeout_ms: int | None = None,
            ) -> dict:
                captured.append(queue_timeout_ms)
                return {"evaluation": {"type": "cp", "value": 0}}

        pool = CapturingPool()
        pgn = _pgn(["e4", "e5"], result="*")
        compute_accuracy_from_pgn(
            pgn_text=pgn,
            engine_pool=pool,
            result="draw",
        )
        assert captured, "engine pool was never invoked"
        assert all(t == _RECOMPUTE_QUEUE_TIMEOUT_MS for t in captured), (
            f"every per-ply acquire must use the recompute timeout "
            f"{_RECOMPUTE_QUEUE_TIMEOUT_MS}; got {captured}"
        )


# ---------------------------------------------------------------------------
# PGNACC_RES — _resolve_authoritative_accuracy wrapper
# ---------------------------------------------------------------------------


class _StubGameFinishRequest:
    """Minimal stand-in for ``GameFinishRequest`` carrying the fields
    the resolver reads."""

    def __init__(self, pgn: str, accuracy: float, weaknesses: dict, result: str):
        self.pgn = pgn
        self.accuracy = accuracy
        self.weaknesses = weaknesses
        self.result = result


class _StubAppState:
    def __init__(self, engine_pool=None):
        if engine_pool is not None:
            self.engine_pool = engine_pool


class _StubApp:
    def __init__(self, engine_pool=None):
        self.state = _StubAppState(engine_pool)


class _StubRequest:
    def __init__(self, engine_pool=None):
        self.app = _StubApp(engine_pool)


class TestResolveAuthoritativeAccuracy:
    """PGNACC_RES: events/router._resolve_authoritative_accuracy behavior."""

    def test_falls_back_when_pool_missing(self, caplog):
        """PGNACC_RES_NO_POOL: missing engine_pool → returns client
        values + emits ACC_FALLBACK log."""
        req = _StubGameFinishRequest(
            pgn=_pgn(["e4"], result="*"),
            accuracy=0.95,
            weaknesses={"tactics": 0.5},
            result="draw",
        )
        request = _StubRequest(engine_pool=None)

        with caplog.at_level(logging.INFO):
            acc, wks, source, _ = _resolve_authoritative_accuracy(
                request=request, req=req, player_id="p1"
            )

        assert source == "client"
        assert acc == 0.95
        assert wks == {"tactics": 0.5}
        assert any("ACC_FALLBACK" in rec.message for rec in caplog.records)

    def test_replaces_inflated_client_accuracy(self, caplog):
        """PGNACC_RES_OVERRIDE: a client claiming accuracy=0.95 on a
        blundered game gets overridden by the server's lower value;
        ACC_DIVERGENCE warning fires at ≥ 0.20 delta.

        Same setup as the unit blunder test: Black blundered e5, game
        ends 1-0, player reports loss → inference says player Black.
        """
        pgn = _pgn(["e4", "e5", "Nf3"], result="1-0")
        pool = _FakeEvalPool(
            cp_by_fen={
                _board_after(["e4"]).split(" ")[0]: 0,
                _board_after(["e4", "e5"]).split(" ")[0]: 400,
                _board_after(["e4", "e5", "Nf3"]).split(" ")[0]: 400,
            }
        )
        req = _StubGameFinishRequest(
            pgn=pgn,
            accuracy=0.95,  # the lie
            weaknesses={"tactics": 0.0},  # also the lie
            result="loss",
        )
        request = _StubRequest(engine_pool=pool)

        with caplog.at_level(logging.WARNING):
            acc, wks, source, _ = _resolve_authoritative_accuracy(
                request=request, req=req, player_id="p1"
            )

        assert source == "engine"
        assert acc < 0.5, f"server accuracy {acc} should be < 0.5 for blundered game"
        # Server weaknesses dict shape — phase-keyed since PR #171
        # (was severity-keyed pre-fix, which broke the downstream
        # aggregate_from_weakness_dicts pipeline silently).  At least
        # one phase must carry a non-zero rate for a blundered game.
        assert wks, f"weaknesses must be non-empty for a blundered game, got {wks}"
        assert set(wks.keys()) <= {"opening", "middlegame", "endgame"}, (
            f"weakness keys must be a subset of phase names "
            f"{{opening, middlegame, endgame}}; got {set(wks.keys())} — "
            f"if this fails, the post-PR-171 aggregator pipeline is broken."
        )
        assert any(v > 0 for v in wks.values()), (
            f"blundered game should produce at least one positive phase rate, "
            f"got {wks}"
        )
        # Divergence warning fired (|0.95 - acc| > 0.20).
        assert any("ACC_DIVERGENCE" in rec.message for rec in caplog.records)

    def test_no_divergence_warning_when_client_agrees(self, caplog):
        """PGNACC_RES_NO_DIVERGENCE: when client's accuracy is close
        to the server's recompute, no ACC_DIVERGENCE warning is logged."""
# Clean game — both client and server should land near 1.0.
        pgn = _pgn(["e4", "e5"], result="1-0")
        pool = _FakeEvalPool(cp_by_fen={})
        req = _StubGameFinishRequest(
            pgn=pgn,
            accuracy=0.98,  # near the server's expected 1.0
            weaknesses={"tactics": 0.0},
            result="win",
        )
        request = _StubRequest(engine_pool=pool)

        with caplog.at_level(logging.WARNING):
            _, _, source, _ = _resolve_authoritative_accuracy(
                request=request, req=req, player_id="p1"
            )

        assert source == "engine"
        # No divergence warning — delta should be < 0.20.
        assert not any("ACC_DIVERGENCE" in rec.message for rec in caplog.records)

    def test_zero_player_moves_raises_422(self):
        """PGNACC_RES_ZERO_PLAYER_MOVES: when the recompute runs but
        finds zero player moves (e.g. PGN has only opponent moves for
        the inferred color), the resolver raises HTTPException(422)
        instead of falling back to client values.

        R2 regression: previously this case silently fell back to
        client values, letting a modded client bypass the recompute
        by paying ~2 engine acquires.  The 422 closes that bypass;
        the genuine "engine unavailable" fallback path stays open
        (``pool is None`` branch in the resolver).
        """
        from fastapi import HTTPException

        # Single White ply; player inferred as BLACK (win + 0-1) →
        # losses_cp is empty → moves_analyzed == 0 → 422.
        pgn = _pgn(["e4"], result="0-1")
        pool = _FakeEvalPool(cp_by_fen={})
        req = _StubGameFinishRequest(
            pgn=pgn,
            accuracy=0.95,
            weaknesses={"tactics": 0.0},
            result="win",
        )
        request = _StubRequest(engine_pool=pool)

        with pytest.raises(HTTPException) as exc_info:
            _resolve_authoritative_accuracy(
                request=request, req=req, player_id="p1"
            )
        assert exc_info.value.status_code == 422
        assert "no player moves" in str(exc_info.value.detail).lower()

    def test_falls_back_on_engine_exception(self, caplog):
        """PGNACC_RES_ENGINE_FAIL: engine raising mid-analysis → fall
        back to client values + emit ACC_FALLBACK."""
        pgn = _pgn(["e4"], result="*")
        pool = _FakeEvalPool(cp_by_fen={}, raise_on_call=RuntimeError)
        req = _StubGameFinishRequest(
            pgn=pgn,
            accuracy=0.7,
            weaknesses={"tactics": 0.1},
            result="draw",
        )
        request = _StubRequest(engine_pool=pool)

        with caplog.at_level(logging.INFO):
            acc, wks, source, _ = _resolve_authoritative_accuracy(
                request=request, req=req, player_id="p1"
            )

        assert source == "client"
        assert acc == 0.7
        assert wks == {"tactics": 0.1}
        assert any("ACC_FALLBACK" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _board_after(moves_san: list[str]) -> str:
    """Apply moves to a fresh Board and return the resulting FEN."""
    board = chess.Board()
    for san in moves_san:
        board.push_san(san)
    return board.fen()


# ---------------------------------------------------------------------------
# B1 regression — variable shadow in finish_game
# ---------------------------------------------------------------------------


def test_recent_weakness_loop_does_not_shadow_authoritative():
    """B1: source-level regression pin.  The recent-events loop in
    ``finish_game`` must NOT rebind ``weaknesses`` (the resolver's
    authoritative output) to a parsed prior event's
    ``weaknesses_json``.

    The 2026-05-14 reviewer pass caught the original PR shadowing the
    outer ``weaknesses`` local with the loop variable, which silently
    routed prior-game client-supplied weaknesses into the bandit's
    context vector — re-opening the exact trust gap PR 5 was meant to
    close.  Pinning the rename at the source level catches the bug
    class in any future PR that touches this hot path.
    """
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parent.parent
        / "seca"
        / "events"
        / "router.py"
    ).read_text(encoding="utf-8")
    forbidden = "weaknesses = json.loads(ev.weaknesses_json)"
    assert forbidden not in src, (
        "router.py shadows the authoritative 'weaknesses' local with "
        "a loop variable; rename the loop variable (e.g. 'parsed') "
        "so the resolver's output isn't silently overwritten before "
        "the bandit call.  See pgn_accuracy.py + the PR-5 reviewer "
        "notes for the regression history."
    )
