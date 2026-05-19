"""
Backend tests for ``llm.seca.mistakes.verify.verify_replay_move`` and
the ``POST /training/verify-replay`` HTTP handler.

Pinned invariants
-----------------
 1. VERIFY_BEST_MOVE_PASSES               playing engine-best → is_correct=True, loss=0.
 2. VERIFY_WITHIN_THRESHOLD_PASSES        loss <= 30 cp → is_correct=True.
 3. VERIFY_OVER_THRESHOLD_FAILS           loss > 30 cp → is_correct=False (no error).
 4. VERIFY_BLACK_TO_MOVE                  POV math is correct when player_color is BLACK.
 5. VERIFY_INVALID_FEN_RAISES             malformed FEN → VerifyError (router → 400).
 6. VERIFY_INVALID_UCI_RAISES             unparseable UCI → VerifyError.
 7. VERIFY_ILLEGAL_MOVE_RAISES            legal UCI shape but not legal here → VerifyError.
 8. VERIFY_ENGINE_UNAVAILABLE             pool acquire timeout → EngineUnavailable (router → 503).
 9. VERIFY_RETURNS_ENGINE_BEST            engine_best_uci surfaces the move the pool returns.
10. VERIFY_REPORTED_LOSS_IS_SIGNED        eval_loss_cp == e_best - e_user (player POV).
11. ROUTE_REQUIRES_POOL                   no pool injected → 503 with stable detail.
12. ROUTE_SCHEMA_REJECTS_BLANK_FIELDS     blank fen / blank move_uci → 400.
13. ROUTE_SCHEMA_LENGTH_CAPS              over-long fen / move_uci → 400.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass

import chess
import chess.engine
import pytest
from pydantic import ValidationError

from llm.seca.mistakes import router as mistakes_router_module
from llm.seca.mistakes.router import VerifyReplayRequest, set_engine_pool
from llm.seca.mistakes.verify import (
    VERIFY_THRESHOLD_CP,
    EngineUnavailable,
    VerifyError,
    verify_replay_move,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakePoolSettings:
    queue_timeout_ms: int = 50


class _FakeEngine:
    """Stand-in for chess.engine.SimpleEngine.  ``analyse`` consults a
    move-keyed lookup map keyed on the player's intended move; the
    fixture decides what eval (and best-move) each branch reports.
    """

    def __init__(
        self,
        *,
        engine_best_uci: str,
        # eval-from-side-to-move POV, in centipawns, for the
        # PRE-USER-MOVE position.  This is what info["score"].pov(player)
        # would report after best play continues.
        e_best_pov_cp: int,
        # eval-from-the-opponent's POV, in centipawns, AFTER the user
        # makes the move keyed in this map.  The verifier flips this
        # back to player POV via .pov(player_color).
        e_user_pov_opponent_cp_by_uci: dict[str, int] | None = None,
        raise_on_analyse: type[Exception] | None = None,
    ) -> None:
        self.engine_best_uci = engine_best_uci
        self.e_best_pov_cp = e_best_pov_cp
        self.e_user_pov_opponent_cp_by_uci = e_user_pov_opponent_cp_by_uci or {}
        self.raise_on_analyse = raise_on_analyse
        self._call_count = 0

    def analyse(
        self,
        board: chess.Board,
        limit: chess.engine.Limit,
    ) -> dict:
        if self.raise_on_analyse is not None:
            raise self.raise_on_analyse("simulated engine failure")

        self._call_count += 1

        if self._call_count == 1:
            # First call: PRE-user-move position.  Build a PovScore with
            # the eval from current side-to-move (= player) POV.
            score = chess.engine.PovScore(chess.engine.Cp(self.e_best_pov_cp), board.turn)
            pv = [chess.Move.from_uci(self.engine_best_uci)]
            return {"score": score, "pv": pv}

        # Second call: POST-user-move position.  Board.turn is now the
        # opponent.  Look up the LAST move played; that tells us what
        # branch we're on.
        last_move = board.peek().uci()
        eval_from_opponent_pov = self.e_user_pov_opponent_cp_by_uci.get(last_move, 0)
        score = chess.engine.PovScore(chess.engine.Cp(eval_from_opponent_pov), board.turn)
        return {"score": score, "pv": []}


class _FakePool:
    """Stand-in for StockfishEnginePool exposing the
    ``_engines`` queue + ``settings`` + ``_release_engine`` surface
    the verifier reads."""

    def __init__(
        self,
        engine: _FakeEngine | None,
        *,
        acquire_raises: type[Exception] | None = None,
    ) -> None:
        self.settings = _FakePoolSettings()
        self._engines: queue.Queue = queue.Queue(maxsize=1)
        if engine is not None:
            self._engines.put(engine)
        self._acquire_raises = acquire_raises
        self.released: list[_FakeEngine] = []

    def _release_engine(self, engine) -> None:
        # In the real pool this returns the engine to the queue; in
        # tests we just record it so we can assert release happened.
        if engine is not None:
            self.released.append(engine)

    # The verifier calls ``_engines.get(timeout=...)`` directly; the
    # queue.Queue we hold honours that.  To simulate acquire failure,
    # we replace ``_engines.get`` at fixture-build time.
    def fail_acquire(self) -> None:
        original_get = self._engines.get

        def _raising(*_args, **_kwargs):
            raise queue.Empty("simulated acquire timeout")

        self._engines.get = _raising  # type: ignore[assignment]
        # Restore reference so tests can introspect if needed.
        self._original_get = original_get


# ---------------------------------------------------------------------------
# verify_replay_move() — unit tests
# ---------------------------------------------------------------------------


# Starting FEN — White to move; many legal moves; deterministic.
_START_FEN = chess.STARTING_FEN


class TestVerifyReplayMove:
    def test_best_move_passes(self):
        """VERIFY_BEST_MOVE_PASSES — playing the engine's preferred
        move yields loss=0 → is_correct=True."""
        # Engine likes e2e4 with eval +30 cp; user plays e2e4; after
        # the move (opponent's turn), opponent POV eval is -30 cp
        # (mirror of player's +30) so player POV via .pov() is +30.
        engine = _FakeEngine(
            engine_best_uci="e2e4",
            e_best_pov_cp=30,
            e_user_pov_opponent_cp_by_uci={"e2e4": -30},
        )
        pool = _FakePool(engine)
        result = verify_replay_move(_START_FEN, "e2e4", pool)  # type: ignore[arg-type]
        assert result.is_correct is True
        assert result.eval_loss_cp == 0
        assert result.engine_best_uci == "e2e4"
        # Engine must have been released back to the pool.
        assert pool.released == [engine]

    def test_within_threshold_passes(self):
        """VERIFY_WITHIN_THRESHOLD_PASSES — loss exactly at
        VERIFY_THRESHOLD_CP must be accepted (<= comparison, not <).
        Pins the boundary so future refactors don't silently shift it.
        """
        # Engine prefers e2e4 (eval +50); user plays a different move
        # whose post-move opponent eval is -20 (= +20 from player POV).
        # loss = 50 - 20 = 30 == threshold.
        engine = _FakeEngine(
            engine_best_uci="e2e4",
            e_best_pov_cp=50,
            e_user_pov_opponent_cp_by_uci={"d2d4": -20},
        )
        pool = _FakePool(engine)
        result = verify_replay_move(_START_FEN, "d2d4", pool)  # type: ignore[arg-type]
        assert result.is_correct is True
        assert result.eval_loss_cp == VERIFY_THRESHOLD_CP
        assert result.engine_best_uci == "e2e4"

    def test_over_threshold_fails(self):
        """VERIFY_OVER_THRESHOLD_FAILS — loss > 30 → is_correct=False;
        NOT an error, the response carries is_correct=False so the UI
        shows "try again"."""
        # Engine likes e2e4 (eval +30); user plays a worse move whose
        # post-move opponent eval is +200 (= -200 from player POV).
        # loss = 30 - (-200) = 230 cp > threshold.
        engine = _FakeEngine(
            engine_best_uci="e2e4",
            e_best_pov_cp=30,
            e_user_pov_opponent_cp_by_uci={"h2h4": 200},
        )
        pool = _FakePool(engine)
        result = verify_replay_move(_START_FEN, "h2h4", pool)  # type: ignore[arg-type]
        assert result.is_correct is False
        assert result.eval_loss_cp == 230

    def test_black_to_move_pov_math(self):
        """VERIFY_BLACK_TO_MOVE — the POV math must flip correctly
        when the player is Black.  Pin against a refactor that
        accidentally hard-codes the player to WHITE."""
        # Position after 1.e4 — Black to move.  Black's best is e7e5
        # with eval -20 cp (black slightly better).
        fen_after_e4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        engine = _FakeEngine(
            engine_best_uci="e7e5",
            # Eval from Black's POV (current side to move) is +20 cp;
            # python-chess Cp on PovScore uses the supplied turn.
            e_best_pov_cp=20,
            e_user_pov_opponent_cp_by_uci={
                # Black plays e7e5; after the move it's White's turn.
                # White POV opponent eval = -20 (white is slightly
                # worse off mirroring black's +20).
                "e7e5": -20,
            },
        )
        pool = _FakePool(engine)
        result = verify_replay_move(fen_after_e4, "e7e5", pool)  # type: ignore[arg-type]
        assert result.is_correct is True
        assert result.eval_loss_cp == 0
        assert result.engine_best_uci == "e7e5"

    def test_invalid_fen_raises(self):
        """VERIFY_INVALID_FEN_RAISES."""
        pool = _FakePool(_FakeEngine(engine_best_uci="e2e4", e_best_pov_cp=0))
        with pytest.raises(VerifyError, match="invalid FEN"):
            verify_replay_move("not a fen", "e2e4", pool)  # type: ignore[arg-type]

    def test_invalid_uci_raises(self):
        """VERIFY_INVALID_UCI_RAISES."""
        pool = _FakePool(_FakeEngine(engine_best_uci="e2e4", e_best_pov_cp=0))
        with pytest.raises(VerifyError, match="invalid UCI"):
            verify_replay_move(_START_FEN, "garbage", pool)  # type: ignore[arg-type]

    def test_illegal_move_raises(self):
        """VERIFY_ILLEGAL_MOVE_RAISES — UCI parses but the move isn't
        legal in the given position."""
        pool = _FakePool(_FakeEngine(engine_best_uci="e2e4", e_best_pov_cp=0))
        # e2e5 is a legal UCI string but the pawn can't jump two
        # squares to e5 from e2 in one ply.
        with pytest.raises(VerifyError, match="not legal"):
            verify_replay_move(_START_FEN, "e2e5", pool)  # type: ignore[arg-type]

    def test_engine_unavailable_when_acquire_times_out(self):
        """VERIFY_ENGINE_UNAVAILABLE — pool acquire timing out maps
        to EngineUnavailable, distinct from any caller-error path."""
        pool = _FakePool(_FakeEngine(engine_best_uci="e2e4", e_best_pov_cp=0))
        pool.fail_acquire()
        with pytest.raises(EngineUnavailable, match="acquire"):
            verify_replay_move(_START_FEN, "e2e4", pool)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# VerifyReplayRequest schema
# ---------------------------------------------------------------------------


class TestVerifyReplayRequestSchema:
    def test_blank_fen_rejected(self):
        """ROUTE_SCHEMA_REJECTS_BLANK_FIELDS — blank fen → 400."""
        with pytest.raises(ValidationError, match="fen must not be empty"):
            VerifyReplayRequest(fen="   ", move_uci="e2e4")

    def test_blank_move_uci_rejected(self):
        with pytest.raises(ValidationError, match="move_uci must not be empty"):
            VerifyReplayRequest(fen=_START_FEN, move_uci="")

    def test_fen_length_cap(self):
        """ROUTE_SCHEMA_LENGTH_CAPS — over-long fen rejected."""
        with pytest.raises(ValidationError, match="fen must be at most"):
            VerifyReplayRequest(fen="x" * 250, move_uci="e2e4")

    def test_move_uci_length_cap(self):
        with pytest.raises(ValidationError, match="move_uci must be at most"):
            VerifyReplayRequest(fen=_START_FEN, move_uci="x" * 12)

    def test_happy_path(self):
        req = VerifyReplayRequest(fen=_START_FEN, move_uci="e2e4")
        assert req.fen == _START_FEN
        assert req.move_uci == "e2e4"


# ---------------------------------------------------------------------------
# Pool injection (router-level)
# ---------------------------------------------------------------------------


class TestPoolInjection:
    """The router reads the engine pool from a module-global set by
    ``set_engine_pool`` at lifespan startup.  Without injection the
    handler must 503 with a stable detail string the Android client
    can recognise."""

    def setup_method(self) -> None:
        # Reset before each test — other tests in the file don't rely
        # on the global but we want the assertion to be honest.
        set_engine_pool(None)

    def teardown_method(self) -> None:
        set_engine_pool(None)

    def test_require_pool_raises_when_unset(self):
        """ROUTE_REQUIRES_POOL — _require_pool() returns a 503 when
        the global is unset.  Pins the injection contract: tests can
        simulate "pool not started" by leaving the global None."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            mistakes_router_module._require_pool()
        assert exc_info.value.status_code == 503
        assert "engine pool unavailable" in str(exc_info.value.detail)

    def test_set_engine_pool_round_trips(self):
        """set_engine_pool(p) followed by _require_pool() returns p."""
        pool = _FakePool(_FakeEngine(engine_best_uci="e2e4", e_best_pov_cp=0))
        set_engine_pool(pool)  # type: ignore[arg-type]
        assert mistakes_router_module._require_pool() is pool
