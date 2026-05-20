"""
Backend tests for ``llm.seca.mistakes.detector.find_first_mistake``.

The detector is engine-call-free — it takes the ``losses_cp`` already
produced by ``compute_accuracy_from_pgn`` and walks the PGN to find
the FIRST position whose loss clears ``MIN_MISTAKE_LOSS_CP``.  Tests
cover the wiring + edge cases that can't be observed from the
higher-level /game/finish integration tests.

Pinned invariants
-----------------
 1. DETECT_RETURNS_NONE_FOR_CLEAN_GAME       no loss >= 150 cp → None.
 2. DETECT_FINDS_WHITE_FIRST_MISTAKE         player WHITE; first above-threshold → correct FEN + UCI + move_number.
 3. DETECT_FINDS_BLACK_FIRST_MISTAKE         player BLACK; first above-threshold → correct FEN + UCI + move_number.
 4. DETECT_PICKS_FIRST_NOT_LARGEST           multiple mistakes → first above threshold wins, larger later losses ignored.
 5. DETECT_SKIPS_BELOW_THRESHOLD             sub-threshold opener walked past; later above-threshold returned.
 6. DETECT_MOVE_NUMBER_IS_1_INDEXED          first player move = ``move_number=1``.
 7. DETECT_EMPTY_LOSSES_RETURNS_NONE         empty list → None.
 8. DETECT_MALFORMED_PGN_RETURNS_NONE        bad PGN → None (not raises).
 9. DETECT_THRESHOLD_BOUNDARY                loss exactly at MIN_MISTAKE_LOSS_CP → surfaced.
10. DETECT_THRESHOLD_BELOW                   loss at MIN_MISTAKE_LOSS_CP - 1 → None.
11. DETECT_LOSSES_OVERRUN_OUT_OF_RANGE       losses_cp drift past PGN, only out-of-range entries clear → None.
12. DETECT_OVERRUN_RECOVERS_WHEN_IN_RANGE    losses_cp drift past PGN, but an in-range entry clears → that entry wins.
"""

from __future__ import annotations

import chess

from llm.seca.mistakes.detector import (
    MIN_MISTAKE_LOSS_CP,
    FirstMistake,
    find_first_mistake,
)


def _pgn(moves_san: list[str], *, result: str = "*") -> str:
    """Build a minimal PGN.  Mirrors the helper in test_pgn_accuracy.py
    so tests stay byte-identical at the PGN-parser layer."""
    moves = " ".join(moves_san)
    return f"""[Event "Test"]
[Result "{result}"]

{moves} {result}
"""


class TestFindFirstMistake:
    def test_clean_game_returns_none(self):
        """DETECT_RETURNS_NONE_FOR_CLEAN_GAME — no loss clears the
        threshold; nothing worth replaying."""
        # Two player moves, both clean.
        pgn = _pgn(["e4", "e5", "Nf3"])
        losses = (10, 20)
        assert find_first_mistake(pgn, losses, chess.WHITE) is None

    def test_finds_white_first_mistake(self):
        """DETECT_FINDS_WHITE_FIRST_MISTAKE — White is the player; the
        second White move is the (only) mistake.  Detector returns the
        position BEFORE that move + the move actually played +
        move_number=2."""
        # Plies: 1=Nf3 (white), 2=e5 (black), 3=Nxe5 (white blunder).
        # Black's e5 is opponent → not counted in player losses.
        pgn = _pgn(["Nf3", "e5", "Nxe5"])
        # Two player moves: Nf3 (clean) + Nxe5 (the blunder).
        losses = (0, 250)

        result = find_first_mistake(pgn, losses, chess.WHITE)
        assert isinstance(result, FirstMistake)
        assert result.move_number == 2
        assert result.eval_loss_cp == 250
        # FEN before the blunder — White to move, after 1.Nf3 e5.
        board = chess.Board()
        board.push_san("Nf3")
        board.push_san("e5")
        assert result.fen_before == board.fen()
        # Move played at that position — Nxe5 in UCI is f3e5.
        assert result.played_uci == "f3e5"

    def test_finds_black_first_mistake(self):
        """DETECT_FINDS_BLACK_FIRST_MISTAKE — Black is the player; the
        first Black move is the mistake.  move_number=1 because it's
        the player's FIRST half-move (not the first ply of the
        game)."""
        # Plies: 1=e4 (white opponent), 2=Nf6 (black, blunder).
        pgn = _pgn(["e4", "Nf6"])
        losses = (180,)  # one Black ply, one above-threshold loss.

        result = find_first_mistake(pgn, losses, chess.BLACK)
        assert isinstance(result, FirstMistake)
        assert result.move_number == 1
        assert result.eval_loss_cp == 180
        board = chess.Board()
        board.push_san("e4")
        assert result.fen_before == board.fen()
        assert result.played_uci == "g8f6"

    def test_picks_first_not_largest(self):
        """DETECT_PICKS_FIRST_NOT_LARGEST — when there are multiple
        moves above the threshold, the detector returns the FIRST one
        in PGN order, not the largest.  This is the load-bearing
        behavior flip from PR #192's original "biggest loss" picker.
        The pedagogical rationale: later, larger-loss moves are often
        downstream symptoms of the first error — surfacing the first
        teaches the originating mistake."""
        # Three player moves, all above the 150 cp threshold.  Old
        # detector would have returned move 3 (loss=400, the
        # largest); new detector returns move 1 (loss=160, the
        # first).
        pgn = _pgn(["e4", "e5", "d3", "Nc6", "Bg5"])
        losses = (160, 200, 400)

        result = find_first_mistake(pgn, losses, chess.WHITE)
        assert isinstance(result, FirstMistake)
        # First above-threshold is index 0 (move 1, loss 160) — NOT
        # the biggest (move 3, loss 400).
        assert result.move_number == 1
        assert result.eval_loss_cp == 160
        # FEN before move 1 (e4) — the starting position.
        assert result.fen_before == chess.Board().fen()
        assert result.played_uci == "e2e4"

    def test_skips_below_threshold(self):
        """DETECT_SKIPS_BELOW_THRESHOLD — sub-threshold losses earlier
        in the game are walked past until the detector reaches the
        first above-threshold move.  Distinct from
        ``picks_first_not_largest`` (where every entry was already
        above threshold): this proves the threshold check actually
        skips clean / minor moves."""
        # Plies: Nf3 (clean), e5 (opponent), d3 (clean), Nc6 (opp),
        # Bg5 (the mistake).  Player WHITE.
        pgn = _pgn(["Nf3", "e5", "d3", "Nc6", "Bg5"])
        # Three player moves: 0 (clean, < threshold), 80 (still <
        # threshold), 250 (clears threshold).
        losses = (0, 80, 250)

        result = find_first_mistake(pgn, losses, chess.WHITE)
        assert isinstance(result, FirstMistake)
        assert result.move_number == 3
        assert result.eval_loss_cp == 250
        # FEN before Bg5 — White to move, after 1.Nf3 e5 2.d3 Nc6.
        board = chess.Board()
        for san in ("Nf3", "e5", "d3", "Nc6"):
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

        result = find_first_mistake(pgn, losses, chess.WHITE)
        assert result is not None
        assert result.eval_loss_cp == MIN_MISTAKE_LOSS_CP

    def test_threshold_boundary_below(self):
        """DETECT_THRESHOLD_BELOW — one cp below the threshold is
        NOT surfaced.  Same boundary pin from the other direction."""
        pgn = _pgn(["Nf3", "e5", "Nxe5"])
        losses = (0, MIN_MISTAKE_LOSS_CP - 1)

        assert find_first_mistake(pgn, losses, chess.WHITE) is None

    def test_empty_losses_returns_none(self):
        """DETECT_EMPTY_LOSSES_RETURNS_NONE — empty list → no mistake.
        Covers the AccuracyAnalysis fallback path where
        ``losses_cp = ()``."""
        pgn = _pgn(["e4", "e5"])
        assert find_first_mistake(pgn, (), chess.WHITE) is None
        assert find_first_mistake(pgn, [], chess.WHITE) is None

    def test_malformed_pgn_returns_none(self):
        """DETECT_MALFORMED_PGN_RETURNS_NONE — bad PGN must NOT raise
        out of /game/finish; detector silently degrades to None.
        Pinned because the route handler's broad ``except`` is the only
        backstop and we want defence-in-depth at the detector layer."""
        # python-chess's PGN parser is lenient; an empty string parses
        # to a Game with no moves.  ``losses_cp`` claims one player
        # move, but the PGN yields none → the walk completes with
        # player_moves_seen=0 and no return.
        result = find_first_mistake("", (250,), chess.WHITE)
        assert result is None

        # Garbage-but-non-empty input also yields no parsable mainline.
        result = find_first_mistake("not a pgn at all", (250,), chess.WHITE)
        assert result is None

    def test_losses_overrun_out_of_range_returns_none(self):
        """DETECT_LOSSES_OVERRUN_OUT_OF_RANGE — when losses_cp claims
        more player moves than the PGN actually has AND only the
        out-of-range entries clear the threshold, the detector
        gracefully returns None (with a drift warning logged)
        instead of returning a wrong move.  Defensive against
        losses_cp/PGN drift bugs upstream in the accuracy
        recompute."""
        # PGN has 2 player moves (e4, Nf3); losses_cp claims 3, with
        # the only above-threshold entry at index 2 (which doesn't
        # exist in the PGN).  The detector walks both in-range
        # entries (both clean), then reports drift and returns None.
        pgn = _pgn(["e4", "e5", "Nf3"])
        losses = (0, 100, 250)

        result = find_first_mistake(pgn, losses, chess.WHITE)
        assert result is None

    def test_overrun_recovers_when_in_range_match_exists(self):
        """DETECT_OVERRUN_RECOVERS_WHEN_IN_RANGE — losses_cp drift
        past the PGN's player-move count no longer hides an earlier
        above-threshold match.  Old "biggest" detector would index
        into the drift entry, fail to find it in the PGN, and return
        None even though a perfectly good in-range match existed.
        New "first" detector short-circuits on the in-range match
        and never tries the drift index."""
        # PGN has 2 player moves; losses_cp claims 3.  The drift
        # entry (index 2, loss=400) doesn't matter because index 0
        # already clears the threshold.
        pgn = _pgn(["e4", "e5", "Nf3"])
        losses = (200, 100, 400)

        result = find_first_mistake(pgn, losses, chess.WHITE)
        assert isinstance(result, FirstMistake)
        assert result.move_number == 1
        assert result.eval_loss_cp == 200
        assert result.fen_before == chess.Board().fen()
        assert result.played_uci == "e2e4"


class TestFirstMistakeDataclass:
    """Smoke checks that FirstMistake stays an immutable, simple
    dataclass.  A future contributor swapping it for a mutable class
    or stripping fields would break the /game/finish response shape."""

    def test_is_frozen(self):
        m = FirstMistake(
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
        raise AssertionError("FirstMistake must be a frozen dataclass")

    def test_fields_are_typed(self):
        m = FirstMistake(
            fen_before="dummy",
            played_uci="e2e4",
            move_number=1,
            eval_loss_cp=200,
        )
        assert isinstance(m.fen_before, str)
        assert isinstance(m.played_uci, str)
        assert isinstance(m.move_number, int)
        assert isinstance(m.eval_loss_cp, int)
