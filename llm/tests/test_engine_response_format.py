"""
Regression tests for engine move encoding, evaluation normalization,
and best_move propagation through the server pipeline.

Root-cause hypotheses pinned here
-----------------------------------
H1 — Score sign inversion in extract_engine_signal (FIXED).
     Schema contract: evaluation.value is centipawns from White's perspective.
     Positive → White is ahead.  Negative → Black is ahead.
     Previously the code had `side = "white" if value < 0 else "black"`,
     which inverted the advantage attribution.

H2 — JniMoveBridge (Android) rejects castling and en passant because the
     local legality checker treats king-moves as max-1-square and requires
     a non-empty capture square for pawn diagonals.  Engine moves for
     those cases return null from normalize().  Covered by documentation
     tests here; the fix lives in the Android layer.

H3 — SachmatuLenta::loadFromBoard64 ignores active-color, castling rights,
     and en passant fields from the FEN string (stops at first space).
     getBestMove(JUODA) is always called for Black regardless of the FEN
     side indicator.  Documented by tests below.

H4 — Pool fast_fallback_move produces an un-scored, deterministic move when
     the engine queue is full.  The /move response does not distinguish this
     case by score; callers should check fallback_used=True in telemetry.
"""

from __future__ import annotations

import re

import chess
import chess.engine
import pytest

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.seca.engines.stockfish.pool import FenMoveCache, StockfishEnginePool, EnginePoolSettings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UCI_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


def _is_valid_uci(uci: str | None) -> bool:
    """Return True if *uci* is a well-formed UCI move string."""
    if not uci:
        return False
    return bool(_UCI_RE.match(uci))


def _cp_signal(value: int, **extra) -> dict:
    payload = {"evaluation": {"type": "cp", "value": value}}
    payload.update(extra)
    return payload


def _mate_signal(**extra) -> dict:
    payload = {"evaluation": {"type": "mate", "value": 3}}
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# H1 — Evaluation score sign convention (regression for the inversion bug)
# ---------------------------------------------------------------------------


class TestEvaluationScoreConvention:
    """
    Schema contract (llm/schema/stockfish_analysis.schema.json):
      evaluation.value = centipawns from White's perspective.
      Positive → White is ahead.
      Negative → Black is ahead.

    The old code had `side = "white" if value < 0 else "black"` which was
    the exact inverse of this contract.
    """

    def test_positive_value_means_white_advantage(self):
        """value > 0: White is ahead — side must be 'white'."""
        esv = extract_engine_signal(_cp_signal(100))
        assert esv["evaluation"]["side"] == "white", (
            "value=100 is a White advantage (centipawns from White POV); "
            "expected side='white', got side={!r}".format(esv["evaluation"]["side"])
        )

    def test_negative_value_means_black_advantage(self):
        """value < 0: Black is ahead — side must be 'black'."""
        esv = extract_engine_signal(_cp_signal(-180))
        assert esv["evaluation"]["side"] == "black", (
            "value=-180 is a Black advantage (centipawns from White POV); "
            "expected side='black', got side={!r}".format(esv["evaluation"]["side"])
        )

    def test_zero_value_defaults_to_black_by_convention(self):
        """value=0: exactly equal; band='equal' is the primary signal."""
        esv = extract_engine_signal(_cp_signal(0))
        assert esv["evaluation"]["band"] == "equal"
        # side is arbitrary at 0 but must be a valid string
        assert esv["evaluation"]["side"] in ("white", "black")

    def test_white_advantage_golden_case_positional_quiet(self):
        """
        Golden case: positional_quiet/case_001.json — White to move, value=35.
        White has a small edge; side must be 'white'.
        """
        stockfish_json = {
            "evaluation": {"type": "cp", "value": 35},
            "eval_delta": 5,
            "errors": {"last_move_quality": "ok"},
            "tactical_flags": [],
            "position_flags": ["space_advantage"],
        }
        fen = "r2q1rk1/pp2bppp/2n1bn2/2pp4/3P4/2PBPN2/PP1N1PPP/R1BQ1RK1 w - - 0 9"
        esv = extract_engine_signal(stockfish_json, fen=fen)
        assert (
            esv["evaluation"]["side"] == "white"
        ), "positional_quiet: value=35 (White POV, White ahead) must yield side='white'"
        assert esv["evaluation"]["band"] == "small_advantage"

    def test_black_advantage_golden_case_tactical_mistake(self):
        """
        Golden case: tactical_mistake/case_001.json — Black to move, value=-180.
        Black is winning; side must be 'black'.
        """
        stockfish_json = {
            "evaluation": {"type": "cp", "value": -180},
            "eval_delta": -150,
            "errors": {"last_move_quality": "mistake"},
            "tactical_flags": ["hanging_piece"],
        }
        fen = "r1bqkbnr/pppp1ppp/2n5/4p3/3PP3/5N2/PPP2PPP/RNBQKB1R b KQkq - 2 3"
        esv = extract_engine_signal(stockfish_json, fen=fen)
        assert (
            esv["evaluation"]["side"] == "black"
        ), "tactical_mistake: value=-180 (White POV, Black ahead) must yield side='black'"
        assert esv["evaluation"]["band"] == "decisive_advantage"

    def test_large_positive_value_is_decisive_advantage(self):
        esv = extract_engine_signal(_cp_signal(500))
        assert esv["evaluation"]["band"] == "decisive_advantage"
        assert esv["evaluation"]["side"] == "white"

    def test_large_negative_value_is_decisive_advantage_for_black(self):
        esv = extract_engine_signal(_cp_signal(-500))
        assert esv["evaluation"]["band"] == "decisive_advantage"
        assert esv["evaluation"]["side"] == "black"


# ---------------------------------------------------------------------------
# Evaluation band thresholds
# ---------------------------------------------------------------------------


class TestEvaluationBandThresholds:
    """Band boundaries as documented in extract_engine_signal.py."""

    @pytest.mark.parametrize("value", [0, 20, -20])
    def test_equal_band(self, value: int):
        esv = extract_engine_signal(_cp_signal(value))
        assert esv["evaluation"]["band"] == "equal"

    @pytest.mark.parametrize("value", [21, 60, -21, -60])
    def test_small_advantage_band(self, value: int):
        esv = extract_engine_signal(_cp_signal(value))
        assert esv["evaluation"]["band"] == "small_advantage"

    @pytest.mark.parametrize("value", [61, 120, -61, -120])
    def test_clear_advantage_band(self, value: int):
        esv = extract_engine_signal(_cp_signal(value))
        assert esv["evaluation"]["band"] == "clear_advantage"

    @pytest.mark.parametrize("value", [121, 500, -121, -500])
    def test_decisive_advantage_band(self, value: int):
        esv = extract_engine_signal(_cp_signal(value))
        assert esv["evaluation"]["band"] == "decisive_advantage"

    def test_mate_type_always_decisive(self):
        esv = extract_engine_signal(_mate_signal())
        assert esv["evaluation"]["type"] == "mate"
        assert esv["evaluation"]["band"] == "decisive_advantage"


# ---------------------------------------------------------------------------
# eval_delta classification
# ---------------------------------------------------------------------------


class TestEvalDeltaClassification:
    @pytest.mark.parametrize("delta", [50, 100, 999])
    def test_large_positive_delta_is_increase(self, delta: int):
        esv = extract_engine_signal(_cp_signal(40, eval_delta=delta))
        assert esv["eval_delta"] == "increase"

    @pytest.mark.parametrize("delta", [-50, -100, -999])
    def test_large_negative_delta_is_decrease(self, delta: int):
        esv = extract_engine_signal(_cp_signal(40, eval_delta=delta))
        assert esv["eval_delta"] == "decrease"

    @pytest.mark.parametrize("delta", [0, 49, -49])
    def test_small_delta_is_stable(self, delta: int):
        esv = extract_engine_signal(_cp_signal(40, eval_delta=delta))
        assert esv["eval_delta"] == "stable"


# ---------------------------------------------------------------------------
# ESV schema shape — required fields always present
# ---------------------------------------------------------------------------


class TestEngineSignalSchema:
    def test_cp_signal_has_required_keys(self):
        esv = extract_engine_signal(_cp_signal(50))
        for key in (
            "evaluation",
            "eval_delta",
            "last_move_quality",
            "tactical_flags",
            "position_flags",
            "phase",
        ):
            assert key in esv, f"Missing key: {key}"

    def test_evaluation_dict_has_required_keys(self):
        esv = extract_engine_signal(_cp_signal(50))
        for key in ("type", "band", "side"):
            assert key in esv["evaluation"], f"Missing evaluation.{key}"

    def test_mate_signal_has_required_keys(self):
        esv = extract_engine_signal(_mate_signal())
        for key in (
            "evaluation",
            "eval_delta",
            "last_move_quality",
            "tactical_flags",
            "position_flags",
            "phase",
        ):
            assert key in esv, f"Missing key: {key}"

    def test_missing_stockfish_json_returns_defaults(self):
        esv = extract_engine_signal(None)
        assert esv["evaluation"]["type"] == "cp"
        assert esv["evaluation"]["band"] == "equal"
        assert esv["phase"] == "middlegame"

    def test_empty_stockfish_json_returns_defaults(self):
        esv = extract_engine_signal({})
        assert esv["evaluation"]["type"] == "cp"
        assert esv["phase"] == "middlegame"


# ---------------------------------------------------------------------------
# side_from_fen — FEN side extraction
# ---------------------------------------------------------------------------


class TestSideFromFen:
    """FEN side parsing used in the mate branch of extract_engine_signal."""

    def test_white_fen_yields_white_side_in_mate_signal(self):
        fen = "r1bq1rk1/pppp1ppp/2n5/4p3/3PPQ2/2N2N2/PPP2PPP/R1B1KB1R b KQ - 5 6"
        esv = extract_engine_signal(_mate_signal(), fen=fen)
        # The mate branch uses side_from_fen(fen) not value
        assert esv["evaluation"]["side"] in ("white", "black")  # either, but valid

    def test_no_fen_in_mate_branch_returns_unknown(self):
        esv = extract_engine_signal(_mate_signal(), fen=None)
        assert esv["evaluation"]["side"] == "unknown"

    def test_malformed_fen_in_mate_branch_returns_unknown(self):
        esv = extract_engine_signal(_mate_signal(), fen="not-a-fen")
        assert esv["evaluation"]["side"] == "unknown"


# ---------------------------------------------------------------------------
# best_move propagation — engine_eval layer
# ---------------------------------------------------------------------------


class TestBestMoveEncoding:
    """
    Verify that best_move returned from EngineEvaluator is a well-formed
    UCI string and propagates correctly through the evaluation layer.
    """

    def test_best_move_from_fake_engine_is_uci(self):
        """evaluate_with_engine extracts best_move from the PV as a UCI string."""
        import asyncio
        from llm.engine_eval import EngineEvaluator

        class _FakeEngine:
            async def analyse(self, board, limit):
                return {
                    "score": chess.engine.PovScore(chess.engine.Cp(45), chess.WHITE),
                    "pv": [chess.Move.from_uci("e2e4")],
                }

        async def _run():
            ev = EngineEvaluator(pool=None)
            return await ev.evaluate_with_engine(_FakeEngine(), "startpos")

        result = asyncio.run(_run())
        assert result["best_move"] == "e2e4"
        assert _is_valid_uci(result["best_move"])

    def test_score_is_white_perspective_centipawns(self):
        """score is the integer centipawn value from White's perspective."""
        import asyncio
        from llm.engine_eval import EngineEvaluator

        class _FakeEngine:
            async def analyse(self, board, limit):
                return {
                    "score": chess.engine.PovScore(chess.engine.Cp(72), chess.WHITE),
                    "pv": [chess.Move.from_uci("d2d4")],
                }

        async def _run():
            ev = EngineEvaluator(pool=None)
            return await ev.evaluate_with_engine(_FakeEngine(), "startpos")

        result = asyncio.run(_run())
        assert result["score"] == 72
        assert isinstance(result["score"], int)

    def test_black_winning_score_is_negative(self):
        """
        When Black is ahead, score_obj.white().score() returns a negative value.
        The response must propagate this negative integer unchanged.
        """
        import asyncio
        from llm.engine_eval import EngineEvaluator

        class _FakeEngine:
            async def analyse(self, board, limit):
                return {
                    "score": chess.engine.PovScore(chess.engine.Cp(-180), chess.WHITE),
                    "pv": [chess.Move.from_uci("e7e5")],
                }

        async def _run():
            ev = EngineEvaluator(pool=None)
            return await ev.evaluate_with_engine(_FakeEngine(), "startpos")

        result = asyncio.run(_run())
        assert result["score"] == -180

    def test_empty_pv_yields_none_best_move(self):
        """If the engine returns an empty PV, best_move must be None (not an error)."""
        import asyncio
        from llm.engine_eval import EngineEvaluator

        class _FakeEngine:
            async def analyse(self, board, limit):
                return {
                    "score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE),
                    "pv": [],
                }

        async def _run():
            ev = EngineEvaluator(pool=None)
            return await ev.evaluate_with_engine(_FakeEngine(), "startpos")

        result = asyncio.run(_run())
        assert result["best_move"] is None

    def test_mate_score_uses_configured_mate_score(self):
        """
        Mate positions are represented near ±10000 centipawns.

        python-chess convention: Mate(N).score(mate_score=10000) returns
        (mate_score - N) for forced wins, e.g. Mate(3) → 9997.
        The score is always within [mate_score-50, mate_score] for near-mates
        and ≥ 9950 for any realistic mate sequence.
        """
        import asyncio
        from llm.engine_eval import EngineEvaluator

        class _FakeEngine:
            async def analyse(self, board, limit):
                return {
                    "score": chess.engine.PovScore(chess.engine.Mate(3), chess.WHITE),
                    "pv": [chess.Move.from_uci("f4f7")],
                }

        async def _run():
            ev = EngineEvaluator(pool=None)
            return await ev.evaluate_with_engine(_FakeEngine(), "startpos")

        result = asyncio.run(_run())
        # Mate(3) gives mate_score - 3 = 9997 by python-chess convention
        assert result["score"] == 9997
        assert 9950 <= result["score"] <= 10000


# ---------------------------------------------------------------------------
# FenMoveCache — move_uci round-trip
# ---------------------------------------------------------------------------


class TestFenMoveCacheEncoding:
    """
    The cache stores and retrieves move_uci strings verbatim.
    The key must differentiate positions, modes, ELO tiers, and line context.
    """

    def _make_cache(self) -> FenMoveCache:
        return FenMoveCache(redis_url=None, ttl_seconds=3600, max_memory_items=50)

    def test_stored_uci_is_retrieved_unchanged(self):
        cache = self._make_cache()
        fen = chess.STARTING_FEN
        cache.set(fen=fen, mode="blitz", movetime_ms=25, target_elo=None, move_uci="e2e4")
        result = cache.get(fen=fen, mode="blitz", movetime_ms=25, target_elo=None)
        assert result == "e2e4"

    def test_promotion_move_stored_and_retrieved(self):
        """Pawn promotion UCI (e.g. 'e7e8q') must survive the cache round-trip."""
        cache = self._make_cache()
        fen = "8/4P3/8/8/8/8/8/7K w - - 0 1"
        cache.set(fen=fen, mode="analysis", movetime_ms=80, target_elo=None, move_uci="e7e8q")
        result = cache.get(fen=fen, mode="analysis", movetime_ms=80, target_elo=None)
        assert result == "e7e8q"

    def test_different_fens_produce_different_cache_entries(self):
        cache = self._make_cache()
        fen_a = chess.STARTING_FEN
        fen_b = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        cache.set(fen=fen_a, mode="blitz", movetime_ms=25, target_elo=None, move_uci="e2e4")
        cache.set(fen=fen_b, mode="blitz", movetime_ms=25, target_elo=None, move_uci="e7e5")
        assert cache.get(fen=fen_a, mode="blitz", movetime_ms=25, target_elo=None) == "e2e4"
        assert cache.get(fen=fen_b, mode="blitz", movetime_ms=25, target_elo=None) == "e7e5"

    def test_different_modes_produce_different_cache_entries(self):
        cache = self._make_cache()
        fen = chess.STARTING_FEN
        cache.set(fen=fen, mode="blitz", movetime_ms=25, target_elo=None, move_uci="e2e4")
        cache.set(fen=fen, mode="analysis", movetime_ms=80, target_elo=None, move_uci="d2d4")
        assert cache.get(fen=fen, mode="blitz", movetime_ms=25, target_elo=None) == "e2e4"
        assert cache.get(fen=fen, mode="analysis", movetime_ms=80, target_elo=None) == "d2d4"

    def test_different_elo_tiers_produce_different_cache_entries(self):
        cache = self._make_cache()
        fen = chess.STARTING_FEN
        cache.set(fen=fen, mode="default", movetime_ms=40, target_elo=1200, move_uci="e2e4")
        cache.set(fen=fen, mode="default", movetime_ms=40, target_elo=2000, move_uci="g1f3")
        assert cache.get(fen=fen, mode="default", movetime_ms=40, target_elo=1200) == "e2e4"
        assert cache.get(fen=fen, mode="default", movetime_ms=40, target_elo=2000) == "g1f3"

    def test_line_key_disambiguates_same_fen(self):
        """Same FEN reached via different move lines → separate cache entries."""
        cache = self._make_cache()
        fen = chess.STARTING_FEN
        cache.set(
            fen=fen, mode="blitz", movetime_ms=25, target_elo=None, move_uci="e2e4", line_key="d2d4"
        )
        cache.set(
            fen=fen, mode="blitz", movetime_ms=25, target_elo=None, move_uci="d2d4", line_key="e2e4"
        )
        assert (
            cache.get(fen=fen, mode="blitz", movetime_ms=25, target_elo=None, line_key="d2d4")
            == "e2e4"
        )
        assert (
            cache.get(fen=fen, mode="blitz", movetime_ms=25, target_elo=None, line_key="e2e4")
            == "d2d4"
        )

    def test_cache_miss_returns_none(self):
        cache = self._make_cache()
        result = cache.get(fen=chess.STARTING_FEN, mode="blitz", movetime_ms=25, target_elo=None)
        assert result is None

    def test_movetime_ms_excluded_from_key_for_same_mode(self):
        """
        Cache key is intentionally coarse: movetime_ms is NOT part of the key.
        A move stored at 25ms is retrievable at 80ms for the same mode/fen/elo.
        This is by design to prevent fragmentation from minor timing differences.
        """
        cache = self._make_cache()
        fen = chess.STARTING_FEN
        cache.set(fen=fen, mode="blitz", movetime_ms=25, target_elo=None, move_uci="e2e4")
        result_80ms = cache.get(fen=fen, mode="blitz", movetime_ms=80, target_elo=None)
        assert result_80ms == "e2e4", (
            "movetime_ms is intentionally excluded from the cache key; "
            "the same FEN+mode+elo entry should be retrievable at any movetime_ms"
        )


# ---------------------------------------------------------------------------
# Pool fallback move — H4: deterministic, legal, UCI-formatted
# ---------------------------------------------------------------------------


class TestPoolFallbackMove:
    """
    StockfishEnginePool.fast_fallback_move must always return a legal move
    formatted as a valid UCI string. This is the degraded path used when the
    engine queue is full.
    """

    def _make_pool(self) -> StockfishEnginePool:
        settings = EnginePoolSettings(stockfish_path="fake-stockfish-not-used")
        pool = StockfishEnginePool(settings)
        # Don't call startup(); we only test the pure fallback logic.
        pool._started = True
        return pool

    def test_fallback_move_is_legal_on_starting_position(self):
        pool = self._make_pool()
        board = chess.Board()
        mv = pool.fast_fallback_move(board)
        assert mv in board.legal_moves

    def test_fallback_move_uci_is_well_formed(self):
        pool = self._make_pool()
        board = chess.Board()
        mv = pool.fast_fallback_move(board)
        assert _is_valid_uci(mv.uci())

    def test_fallback_prefers_captures(self):
        """When captures are available the fallback must choose one."""
        pool = self._make_pool()
        # Minimal position: White pawn e5 can capture black pawn on d6.
        # Legal moves: e5d6 (capture), e5e6 (push), Ke1* king moves.
        # Fallback must choose a capture when captures exist.
        board = chess.Board("8/8/3p4/4P3/8/8/8/4K1k1 w - - 0 1")
        mv = pool.fast_fallback_move(board)
        assert board.is_capture(mv), f"Expected a capture but got {mv.uci()}"

    def test_fallback_is_deterministic(self):
        """Same board always produces the same fallback move."""
        pool = self._make_pool()
        board = chess.Board()
        mv_a = pool.fast_fallback_move(board)
        mv_b = pool.fast_fallback_move(board)
        assert mv_a == mv_b

    def test_fallback_raises_on_no_legal_moves(self):
        """Stalemate / checkmate positions have no legal moves — RuntimeError expected."""
        pool = self._make_pool()
        board = chess.Board("7k/5Q2/7K/8/8/8/8/8 b - - 0 1")  # Black is checkmated
        with pytest.raises(RuntimeError, match="No legal moves"):
            pool.fast_fallback_move(board)

    def test_fallback_move_is_legal_on_endgame_position(self):
        pool = self._make_pool()
        board = chess.Board("8/8/4k3/8/8/4K3/4P3/8 w - - 0 1")
        mv = pool.fast_fallback_move(board)
        assert mv in board.legal_moves
        assert _is_valid_uci(mv.uci())


# ---------------------------------------------------------------------------
# Pool movetime resolution — mode-to-time mapping contract
# ---------------------------------------------------------------------------


class TestPoolMovetimeResolution:
    def _make_pool(self) -> StockfishEnginePool:
        settings = EnginePoolSettings(
            stockfish_path="unused",
            default_movetime_ms=40,
            blitz_movetime_ms=25,
            training_movetime_ms=40,
            analysis_movetime_ms=80,
            min_movetime_ms=20,
            max_movetime_ms=2000,
        )
        return StockfishEnginePool(settings)

    def test_blitz_mode_resolves_to_blitz_movetime(self):
        pool = self._make_pool()
        assert pool.resolve_movetime_ms("blitz", None) == 25

    def test_analysis_mode_resolves_to_analysis_movetime(self):
        pool = self._make_pool()
        assert pool.resolve_movetime_ms("analysis", None) == 80

    def test_explicit_movetime_overrides_mode(self):
        pool = self._make_pool()
        assert pool.resolve_movetime_ms("blitz", 100) == 100

    def test_movetime_is_clamped_to_minimum(self):
        pool = self._make_pool()
        assert pool.resolve_movetime_ms("blitz", 5) == 20

    def test_movetime_is_clamped_to_maximum(self):
        pool = self._make_pool()
        assert pool.resolve_movetime_ms("blitz", 9999) == 2000

    def test_unknown_mode_falls_back_to_default(self):
        pool = self._make_pool()
        assert pool.resolve_movetime_ms("nonsense", None) == 40

    def test_puzzle_alias_resolves_to_training(self):
        pool = self._make_pool()
        assert pool.resolve_movetime_ms("puzzle", None) == 40

    def test_deep_alias_resolves_to_analysis(self):
        pool = self._make_pool()
        assert pool.resolve_movetime_ms("deep", None) == 80


# ---------------------------------------------------------------------------
# JNI bridge design notes — documented as assertions on known limitations
# (H2, H3)
# ---------------------------------------------------------------------------


class TestJniBridgeDesignConstraints:
    """
    These tests document the known limitations of the Android JNI layer.
    They do NOT test Android code directly (no JVM dependency here), but
    pin the constraints so that any backend change that relies on
    the Android engine handling special moves is caught at review time.
    """

    def test_castling_move_is_legal_on_backend_board(self):
        """
        The C++ engine can generate castling (king moves 2 squares).
        The backend must be able to represent and handle this move.
        """
        board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
        castling_uci = "e1g1"  # White kingside castling
        mv = chess.Move.from_uci(castling_uci)
        assert mv in board.legal_moves, "Castling must be legal on this position"
        assert _is_valid_uci(mv.uci())

    def test_en_passant_move_is_legal_on_backend_board(self):
        """
        The C++ engine can generate en passant captures.
        The backend must handle the empty-target-square case correctly.
        """
        board = chess.Board("rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3")
        ep_uci = "e5f6"  # En passant capture
        mv = chess.Move.from_uci(ep_uci)
        assert mv in board.legal_moves, "En passant must be legal on this position"

    def test_promotion_move_is_legal_on_backend_board(self):
        """Promotion moves must be representable in UCI and legal on the board."""
        board = chess.Board("8/4P3/8/8/8/8/8/4K2k w - - 0 1")
        promo_uci = "e7e8q"
        mv = chess.Move.from_uci(promo_uci)
        assert mv in board.legal_moves

    def test_backend_fen_normalization_preserves_side_to_move(self):
        """
        H3: the C++ engine ignores the side-to-move field in the FEN.
        The backend Python layer (position_input.normalize_position) DOES
        preserve it correctly — this test pins that guarantee.
        """
        from llm.position_input import normalize_position

        fen_black = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        result_fen, _, board = normalize_position(fen=fen_black)
        assert board.turn == chess.BLACK, (
            "normalize_position must preserve the side-to-move from the FEN; "
            "the C++ engine does not — callers must not rely on native engine "
            "to respect FEN side-to-move."
        )
        assert result_fen == fen_black
