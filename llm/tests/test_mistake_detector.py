"""
Backend tests for ``llm.seca.mistakes.detector.find_biggest_mistake``.

The detector is engine-call-free — it takes the ``losses_cp`` already
produced by ``compute_accuracy_from_pgn`` and walks the PGN to find
the position immediately before the worst player move.  Tests cover
the wiring + edge cases that can't be observed from the higher-level
/game/finish integration tests.

Pinned invariants
-----------------
 1. DETECT_RETURNS_NONE_FOR_CLEAN_GAME   no loss >= 150 cp → None.
 2. DETECT_FINDS_WHITE_BLUNDER           player WHITE; largest loss → correct FEN + UCI + move_number.
 3. DETECT_FINDS_BLACK_BLUNDER           player BLACK; largest loss → correct FEN + UCI + move_number.
 4. DETECT_PICKS_LARGEST_OF_MANY         several mistakes → returns the biggest, not the first.
 5. DETECT_MOVE_NUMBER_IS_1_INDEXED      first player move = ``move_number=1``.
 6. DETECT_EMPTY_LOSSES_RETURNS_NONE     empty list → None.
 7. DETECT_MALFORMED_PGN_RETURNS_NONE    bad PGN → None (not raises).
 8. DETECT_THRESHOLD_BOUNDARY            loss exactly at MIN_MISTAKE_LOSS_CP → surfaced.
 9. DETECT_THRESHOLD_BELOW               loss at MIN_MISTAKE_LOSS_CP - 1 → None.
10. DETECT_LOSSES_OVERRUN_RETURNS_NONE   losses_cp claims more moves than PGN has → None.
"""

from __future__ import annotations

import chess

from llm.seca.mistakes.detector import (
    MIN_MISTAKE_LOSS_CP,
    BiggestMistake,
    find_biggest_mistake,
)


def _pgn(moves_san: list[str], *, result: str = "*") -> str:
    """Build a minimal PGN.  Mirrors the helper in test_pgn_accuracy.py
    so tests stay byte-identical at the PGN-parser layer."""
    moves = " ".join(moves_san)
    return f"""[Event "Test"]
[Result "{result}"]

{moves} {result}
"""


class TestFindBiggestMistake:
    def test_clean_game_returns_none(self):
        """DETECT_RETURNS_NONE_FOR_CLEAN_GAME — no loss clears the
        threshold; nothing worth replaying."""
        # Two player moves, both clean.
        pgn = _pgn(["e4", "e5", "Nf3"])
        losses = (10, 20)
        assert find_biggest_mistake(pgn, losses, chess.WHITE) is None

    def test_finds_white_blunder(self):
        """DETECT_FINDS_WHITE_BLUNDER — White is the player; second
        White move is the blunder.  Detector returns the position
        BEFORE the blunder + the move actually played + move_number=2."""
        # Plies: 1=Nf3 (white), 2=e5 (black), 3=Nxe5 (white blunder).
        # Black's e5 is opponent → not counted in player losses.
        pgn = _pgn(["Nf3", "e5", "Nxe5"])
        # Two player moves: Nf3 (clean) + Nxe5 (the blunder).
        losses = (0, 250)

        result = find_biggest_mistake(pgn, losses, chess.WHITE)
        assert isinstance(result, BiggestMistake)
        assert result.move_number == 2
        assert result.eval_loss_cp == 250
        # FEN before the blunder — White to move, after 1.Nf3 e5.
        board = chess.Board()
        board.push_san("Nf3")
        board.push_san("e5")
        assert result.fen_before == board.fen()
        # Move played at that position — Nxe5 in UCI is f3e5.
        assert result.played_uci == "f3e5"

    def test_finds_black_blunder(self):
        """DETECT_FINDS_BLACK_BLUNDER — Black is the player; first
        Black move is the blunder.  Move_number=1 because it's the
        player's FIRST half-move (not the first ply of the game)."""
        # Plies: 1=e4 (white opponent), 2=Nf6 (black, blunder).
        pgn = _pgn(["e4", "Nf6"])
        losses = (180,)  # one Black ply, one big loss.

        result = find_biggest_mistake(pgn, losses, chess.BLACK)
        assert isinstance(result, BiggestMistake)
        assert result.move_number == 1
        assert result.eval_loss_cp == 180
        board = chess.Board()
        board.push_san("e4")
        assert result.fen_before == board.fen()
        assert result.played_uci == "g8f6"

    def test_picks_largest_of_many(self):
        """DETECT_PICKS_LARGEST_OF_MANY — when there are multiple
        mistakes above the threshold, the detector returns the worst,
        not the first."""
        # Three player moves; the THIRD has the biggest loss.
        pgn = _pgn(["e4", "e5", "d3", "Nc6", "Bg5"])
        # White moves: e4 (loss=160), d3 (loss=200), Bg5 (loss=400).
        losses = (160, 200, 400)

        result = find_biggest_mistake(pgn, losses, chess.WHITE)
        assert isinstance(result, BiggestMistake)
        assert result.eval_loss_cp == 400
        assert result.move_number == 3
        # FEN before Bg5 — White to move, after 1.e4 e5 2.d3 Nc6.
        board = chess.Board()
        for san in ("e4", "e5", "d3", "Nc6"):
            board.push_san(san)
        assert result.fen_before == board.fen()
        assert result.played_uci == "c1g5"

    def test_threshold_boundary_inclusive(self):
        """DETECT_THRESHOLD_BOUNDARY — a loss exactly at
        MIN_MISTAKE_LOSS_CP IS surfaced (>= comparison, not >).  Pins
        the boundary so a future refactor swapping >= for > doesn't
        silently drop borderline mistakes."""
        pgn = _pgn(["Nf3", "e5", "Nxe5"])
        losses = (0, MIN_MISTAKE_LOSS_CP)

        result = find_biggest_mistake(pgn, losses, chess.WHITE)
        assert result is not None
        assert result.eval_loss_cp == MIN_MISTAKE_LOSS_CP

    def test_threshold_boundary_below(self):
        """DETECT_THRESHOLD_BELOW — one cp below the threshold is
        NOT surfaced.  Same boundary pin from the other direction."""
        pgn = _pgn(["Nf3", "e5", "Nxe5"])
        losses = (0, MIN_MISTAKE_LOSS_CP - 1)

        assert find_biggest_mistake(pgn, losses, chess.WHITE) is None

    def test_empty_losses_returns_none(self):
        """DETECT_EMPTY_LOSSES_RETURNS_NONE — empty list → no mistake.
        Covers the AccuracyAnalysis fallback path where
        ``losses_cp = ()``."""
        pgn = _pgn(["e4", "e5"])
        assert find_biggest_mistake(pgn, (), chess.WHITE) is None
        assert find_biggest_mistake(pgn, [], chess.WHITE) is None

    def test_malformed_pgn_returns_none(self):
        """DETECT_MALFORMED_PGN_RETURNS_NONE — bad PGN must NOT raise
        out of /game/finish; detector silently degrades to None.
        Pinned because the route handler's broad ``except`` is the only
        backstop and we want defence-in-depth at the detector layer."""
        # python-chess's PGN parser is lenient; an empty string parses
        # to a Game with no moves.  ``losses_cp`` claims one player move,
        # but the PGN yields none → the walk-overrun branch returns None.
        result = find_biggest_mistake("", (250,), chess.WHITE)
        assert result is None

        # Garbage-but-non-empty input also yields no parsable mainline.
        result = find_biggest_mistake("not a pgn at all", (250,), chess.WHITE)
        assert result is None

    def test_losses_overrun_returns_none(self):
        """DETECT_LOSSES_OVERRUN_RETURNS_NONE — when losses_cp claims
        more player moves than the PGN actually has, the walk falls
        off the end and the detector returns None instead of indexing
        into a partial-walk state.  Defensive against losses_cp/PGN
        drift bugs upstream in the accuracy recompute."""
        # PGN has 2 player moves (White), losses_cp claims 3 with the
        # biggest at index 2 (which doesn't exist in the PGN).
        pgn = _pgn(["e4", "e5", "Nf3"])
        losses = (0, 100, 250)

        # Index 2 doesn't have a corresponding player move → fall off.
        # The detector should return None (logged + degraded) rather
        # than returning a wrong FEN.  Note: this also happens to clear
        # the index-1 fallback because the detector picks max, not first
        # above-threshold — so the WHOLE detect call gracefully returns
        # None.
        result = find_biggest_mistake(pgn, losses, chess.WHITE)
        assert result is None


class TestBiggestMistakeDataclass:
    """Smoke checks that BiggestMistake stays an immutable, simple
    dataclass.  A future contributor swapping it for a mutable class
    or stripping fields would break the /game/finish response shape."""

    def test_is_frozen(self):
        m = BiggestMistake(
            fen_before="dummy",
            played_uci="e2e4",
            move_number=1,
            eval_loss_cp=200,
        )
        import dataclasses

        # ``frozen=True`` raises FrozenInstanceError on attribute set.
        try:
            m.move_number = 99  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("BiggestMistake must be a frozen dataclass")

    def test_fields_are_typed(self):
        m = BiggestMistake(
            fen_before="dummy",
            played_uci="e2e4",
            move_number=1,
            eval_loss_cp=200,
        )
        assert isinstance(m.fen_before, str)
        assert isinstance(m.played_uci, str)
        assert isinstance(m.move_number, int)
        assert isinstance(m.eval_loss_cp, int)
