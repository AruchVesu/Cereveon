"""
Backend tests for ``llm.seca.mistakes.detector.find_first_mistake``.

The detector is engine-call-free — it takes the ``losses_cp`` and the
player-POV eval-before / eval-after lists already produced by
``compute_accuracy_from_pgn``, walks the PGN, and trips on whichever of
two signals fires first per player move:

 * Single-move delta — ``losses_cp[i] >= MIN_MISTAKE_LOSS_CP`` (150 cp).
 * Cumulative-eval transition — ``eval_before > -LOSING_THRESHOLD_CP``
   AND ``eval_after <= -LOSING_THRESHOLD_CP`` (200 cp) on the same move.

Pinned invariants
-----------------
 1. DETECT_RETURNS_NONE_FOR_CLEAN_GAME       no signal trips → None.
 2. DETECT_FINDS_WHITE_FIRST_MISTAKE         player WHITE; single-move-delta fires.
 3. DETECT_FINDS_BLACK_FIRST_MISTAKE         player BLACK; single-move-delta fires.
 4. DETECT_PICKS_FIRST_NOT_LARGEST           multiple above-threshold; first wins.
 5. DETECT_SKIPS_BELOW_THRESHOLD             sub-threshold losses walked past.
 6. DETECT_MOVE_NUMBER_IS_1_INDEXED          first player move = ``move_number=1``.
 7. DETECT_EMPTY_LOSSES_RETURNS_NONE         empty list → None.
 8. DETECT_MALFORMED_PGN_RETURNS_NONE        bad PGN → None (not raises).
 9. DETECT_THRESHOLD_BOUNDARY_INCLUSIVE      loss == MIN_MISTAKE_LOSS_CP → surfaced.
10. DETECT_THRESHOLD_BELOW                   loss == MIN_MISTAKE_LOSS_CP - 1 → None.
11. DETECT_LOSSES_OVERRUN_OUT_OF_RANGE       drift, no in-range trip → None.
12. DETECT_OVERRUN_RECOVERS_WHEN_IN_RANGE    drift, in-range single-move trips → returned.
13. TRANSITION_PICKS_EARLIER                 slow-burn losses < 150 but eval crosses -200 → returned.
14. TRANSITION_REQUIRES_OK_BEFORE            eval_before already <= -200 → transition doesn't fire.
15. TRANSITION_FALLBACK_TO_SINGLE_MOVE       already-lost player; subsequent >= 150 loss trips fallback.
16. TRANSITION_BOUNDARY_INCLUSIVE            eval_after == -LOSING_THRESHOLD_CP triggers (<= comparison).
17. TRANSITION_BOUNDARY_BEFORE_STRICT        eval_before == -LOSING_THRESHOLD_CP does NOT trigger (> comparison).
18. EARLIER_OF_BOTH_SIGNALS                  both conditions fire on different moves → earlier wins.
"""

from __future__ import annotations

import chess

from llm.seca.mistakes.detector import (
    LOSING_THRESHOLD_CP,
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


def _neutral_evals(n: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Build eval-before / eval-after tuples of length n that NEVER
    trigger the transition signal — both stay strictly above
    ``-LOSING_THRESHOLD_CP``.  Used by tests that only care about
    the single-move-delta signal so the transition path can't
    accidentally fire and change which move is returned."""
    return (0,) * n, (-50,) * n


class TestFindFirstMistake:
    def test_clean_game_returns_none(self):
        """DETECT_RETURNS_NONE_FOR_CLEAN_GAME — no signal trips; nothing
        worth replaying."""
        # Two player moves, both clean.  Eval stays in the OK band the
        # whole time.
        pgn = _pgn(["e4", "e5", "Nf3"])
        losses = (10, 20)
        before, after = _neutral_evals(2)
        assert find_first_mistake(pgn, losses, before, after, chess.WHITE) is None

    def test_finds_white_first_mistake(self):
        """DETECT_FINDS_WHITE_FIRST_MISTAKE — single-move-delta trips
        on the second White move."""
        pgn = _pgn(["Nf3", "e5", "Nxe5"])
        losses = (0, 250)
        # Eval stays above -200 even after the blunder so the
        # transition path doesn't also fire (we're testing the
        # single-move-delta path here).
        before = (0, 0)
        after = (0, -150)

        result = find_first_mistake(pgn, losses, before, after, chess.WHITE)
        assert isinstance(result, FirstMistake)
        assert result.move_number == 2
        assert result.eval_loss_cp == 250
        board = chess.Board()
        board.push_san("Nf3")
        board.push_san("e5")
        assert result.fen_before == board.fen()
        assert result.played_uci == "f3e5"

    def test_finds_black_first_mistake(self):
        """DETECT_FINDS_BLACK_FIRST_MISTAKE — Black is the player; the
        first Black move is the mistake.  move_number=1."""
        pgn = _pgn(["e4", "Nf6"])
        losses = (180,)
        # Eval-after stays above the losing threshold so single-move-
        # delta is the only signal firing.
        before, after = (0,), (-50,)

        result = find_first_mistake(pgn, losses, before, after, chess.BLACK)
        assert isinstance(result, FirstMistake)
        assert result.move_number == 1
        assert result.eval_loss_cp == 180
        board = chess.Board()
        board.push_san("e4")
        assert result.fen_before == board.fen()
        assert result.played_uci == "g8f6"

    def test_picks_first_not_largest(self):
        """DETECT_PICKS_FIRST_NOT_LARGEST — when there are multiple
        moves above the single-move threshold, the detector returns
        the FIRST one in PGN order."""
        pgn = _pgn(["e4", "e5", "d3", "Nc6", "Bg5"])
        losses = (160, 200, 400)
        # Eval stays above -200 throughout so transition can't fire on
        # any earlier move than the single-move-delta would.
        before = (0, 0, 0)
        after = (-100, -100, -150)

        result = find_first_mistake(pgn, losses, before, after, chess.WHITE)
        assert isinstance(result, FirstMistake)
        assert result.move_number == 1
        assert result.eval_loss_cp == 160
        assert result.fen_before == chess.Board().fen()
        assert result.played_uci == "e2e4"

    def test_skips_below_threshold(self):
        """DETECT_SKIPS_BELOW_THRESHOLD — sub-threshold losses earlier
        in the game are walked past until the first above-threshold
        move trips the single-move-delta signal."""
        pgn = _pgn(["Nf3", "e5", "d3", "Nc6", "Bg5"])
        losses = (0, 80, 250)
        # Eval stays above the losing floor on moves 1-2 so transition
        # can't fire there; on move 3 the loss alone clears the
        # single-move threshold.
        before = (0, 0, 0)
        after = (0, -80, -150)

        result = find_first_mistake(pgn, losses, before, after, chess.WHITE)
        assert isinstance(result, FirstMistake)
        assert result.move_number == 3
        assert result.eval_loss_cp == 250
        board = chess.Board()
        for san in ("Nf3", "e5", "d3", "Nc6"):
            board.push_san(san)
        assert result.fen_before == board.fen()
        assert result.played_uci == "c1g5"

    def test_threshold_boundary_inclusive(self):
        """DETECT_THRESHOLD_BOUNDARY_INCLUSIVE — loss == MIN_MISTAKE_LOSS_CP
        IS surfaced via the single-move-delta signal (>= comparison).
        Pin lives here so a future refactor swapping >= for > doesn't
        silently drop borderline mistakes."""
        pgn = _pgn(["Nf3", "e5", "Nxe5"])
        losses = (0, MIN_MISTAKE_LOSS_CP)
        # Keep transition signal quiet on both moves.
        before = (0, 0)
        after = (0, -100)

        result = find_first_mistake(pgn, losses, before, after, chess.WHITE)
        assert result is not None
        assert result.eval_loss_cp == MIN_MISTAKE_LOSS_CP

    def test_threshold_boundary_below(self):
        """DETECT_THRESHOLD_BELOW — one cp below the single-move
        threshold is NOT surfaced (transition signal is also kept
        quiet)."""
        pgn = _pgn(["Nf3", "e5", "Nxe5"])
        losses = (0, MIN_MISTAKE_LOSS_CP - 1)
        before, after = _neutral_evals(2)

        assert find_first_mistake(pgn, losses, before, after, chess.WHITE) is None

    def test_empty_losses_returns_none(self):
        """DETECT_EMPTY_LOSSES_RETURNS_NONE — empty list → no mistake.
        Covers the AccuracyAnalysis fallback path."""
        pgn = _pgn(["e4", "e5"])
        assert find_first_mistake(pgn, (), (), (), chess.WHITE) is None
        assert find_first_mistake(pgn, [], [], [], chess.WHITE) is None

    def test_malformed_pgn_returns_none(self):
        """DETECT_MALFORMED_PGN_RETURNS_NONE — bad PGN must NOT raise
        out of /game/finish; detector silently degrades to None."""
        result = find_first_mistake("", (250,), (0,), (-300,), chess.WHITE)
        assert result is None

        result = find_first_mistake(
            "not a pgn at all", (250,), (0,), (-300,), chess.WHITE
        )
        assert result is None

    def test_losses_overrun_out_of_range_returns_none(self):
        """DETECT_LOSSES_OVERRUN_OUT_OF_RANGE — when losses_cp claims
        more player moves than the PGN actually has AND only the
        out-of-range entries clear the threshold, return None (with
        a drift warning logged)."""
        pgn = _pgn(["e4", "e5", "Nf3"])
        losses = (0, 100, 250)
        # Keep eval signals quiet on the in-range moves so the only
        # potential trip would be on the out-of-range index 2.
        before = (0, 0, 0)
        after = (0, -50, -50)

        result = find_first_mistake(pgn, losses, before, after, chess.WHITE)
        assert result is None

    def test_overrun_recovers_when_in_range_match_exists(self):
        """DETECT_OVERRUN_RECOVERS_WHEN_IN_RANGE — losses_cp drift
        past the PGN's player-move count no longer hides an earlier
        in-range single-move-delta trip."""
        pgn = _pgn(["e4", "e5", "Nf3"])
        losses = (200, 100, 400)
        # eval_after[0] = -199 keeps the transition signal JUST below
        # firing on move 1; the trip in this test comes purely from
        # the single-move-delta path.
        before = (0, 0, 0)
        after = (-199, -50, -50)

        result = find_first_mistake(pgn, losses, before, after, chess.WHITE)
        assert isinstance(result, FirstMistake)
        assert result.move_number == 1
        assert result.eval_loss_cp == 200
        assert result.fen_before == chess.Board().fen()
        assert result.played_uci == "e2e4"

    # ------------------------------------------------------------------
    # Cumulative-eval transition signal (added 2026-05-20 after the
    # king-walk regression — see module docstring).
    # ------------------------------------------------------------------

    def test_transition_picks_earlier_than_single_move_delta(self):
        """TRANSITION_PICKS_EARLIER — the headline case from the
        2026-05-20 king-walk regression: each individual move loses
        less than the single-move threshold (so single-move-delta
        alone would surface a later catastrophic move), but the
        cumulative player-POV eval crosses ``-LOSING_THRESHOLD_CP`` on
        a specific move and THAT move is the lesson."""
        # White player walks the king out: f3, Kf2, Kg3, Kh4.
        pgn = _pgn(["f3", "e5", "Kf2", "Nf6", "Kg3", "d5", "Kh4"])
        # Per-move losses are each < 150 cp until Kh4 collapses the
        # position.  The cumulative eval drifts down: 0 → -80 → -150
        # → -350 → -9743 (mate).  Transition fires on the move that
        # crosses -200, which is move 3 (Kg3): eval_before=-150 (> -200),
        # eval_after=-350 (<= -200).
        losses = (80, 70, 200, 9543)
        before = (0, -80, -150, -350)
        after = (-80, -150, -350, -9743)

        result = find_first_mistake(pgn, losses, before, after, chess.WHITE)
        assert isinstance(result, FirstMistake)
        # Transition fired on move 3 (Kg3) — that's the originating
        # mistake.  Without the transition signal, single-move-delta
        # would have picked move 4 (Kh4, loss=9543) or move 3
        # (loss=200), but the transition is more pedagogically
        # meaningful because it identifies the move that committed
        # the player to a lost position.
        assert result.move_number == 3
        # The single-move loss on the transition move is reported as
        # the eval_loss_cp — even though the transition path tripped,
        # the user still sees how much the single move cost on the
        # replay sheet.
        assert result.eval_loss_cp == 200
        assert result.played_uci == "f2g3"

    def test_transition_requires_ok_before(self):
        """TRANSITION_REQUIRES_OK_BEFORE — when the player was already
        lost before the move (``eval_before <= -LOSING_THRESHOLD_CP``),
        the transition signal does NOT fire because there's nothing
        to "cross" — they were already on the losing side."""
        pgn = _pgn(["Nf3", "e5", "Nxe5"])
        losses = (0, 50)  # both single-move losses sub-threshold
        # Player was already lost before the first move; eval_after
        # stays in losing territory.  Transition can't fire because
        # eval_before <= -200 on every move.  Single-move-delta also
        # doesn't fire (losses are sub-threshold).
        before = (-300, -300)
        after = (-300, -300)

        assert find_first_mistake(pgn, losses, before, after, chess.WHITE) is None

    def test_transition_fallback_to_single_move(self):
        """TRANSITION_FALLBACK_TO_SINGLE_MOVE — when the player was
        already lost (transition can't fire) but later makes a clear
        single-move blunder, the single-move-delta signal still
        catches it."""
        pgn = _pgn(["Nf3", "e5", "Nxe5"])
        losses = (50, 250)
        # Player was already lost from the start; the second move
        # adds another 250 cp of damage on top.  Transition is dead
        # (eval_before always below -LOSING_THRESHOLD_CP) but the
        # single-move-delta still trips on move 2.
        before = (-300, -350)
        after = (-350, -600)

        result = find_first_mistake(pgn, losses, before, after, chess.WHITE)
        assert isinstance(result, FirstMistake)
        assert result.move_number == 2
        assert result.eval_loss_cp == 250

    def test_transition_boundary_inclusive(self):
        """TRANSITION_BOUNDARY_INCLUSIVE — ``eval_after`` exactly equal
        to ``-LOSING_THRESHOLD_CP`` triggers the transition (<=
        comparison, not <).  Pin the boundary."""
        pgn = _pgn(["e4", "e5", "Nf3"])
        losses = (0, 0)
        # Move 1: eval_before=0 (> -200), eval_after=-LOSING_THRESHOLD_CP
        # (exactly).  Transition MUST fire (the player crossed onto the
        # losing edge).
        before = (0, -LOSING_THRESHOLD_CP)
        after = (-LOSING_THRESHOLD_CP, -LOSING_THRESHOLD_CP)

        result = find_first_mistake(pgn, losses, before, after, chess.WHITE)
        assert isinstance(result, FirstMistake)
        assert result.move_number == 1

    def test_transition_boundary_before_strict(self):
        """TRANSITION_BOUNDARY_BEFORE_STRICT — ``eval_before`` exactly
        equal to ``-LOSING_THRESHOLD_CP`` does NOT trigger the
        transition (> comparison, not >=).  The "you were OK" side
        is a strict boundary so a player already on the edge doesn't
        get re-surfaced for every move they make."""
        pgn = _pgn(["e4", "e5"])
        losses = (0,)
        # eval_before sits exactly at the losing-floor boundary;
        # eval_after drops further.  The player was already "lost"
        # going in, so the transition shouldn't claim this as a new
        # crossing.  Single-move loss is 0 so single-move-delta also
        # doesn't fire.
        before = (-LOSING_THRESHOLD_CP,)
        after = (-LOSING_THRESHOLD_CP - 50,)

        assert find_first_mistake(pgn, losses, before, after, chess.WHITE) is None

    def test_earlier_of_both_signals(self):
        """EARLIER_OF_BOTH_SIGNALS — when both the transition and
        single-move-delta signals would fire on DIFFERENT moves,
        the detector returns the earlier one (PGN order)."""
        pgn = _pgn(["Nf3", "e5", "d3", "Nc6", "Bg5"])
        # Move 1: transition fires (eval crosses -200).  Move 3:
        # single-move-delta fires (loss=300).  The detector must
        # return move 1.
        losses = (100, 50, 300)
        before = (0, -250, -300)
        after = (-250, -300, -600)

        result = find_first_mistake(pgn, losses, before, after, chess.WHITE)
        assert isinstance(result, FirstMistake)
        assert result.move_number == 1
        assert result.eval_loss_cp == 100


class TestFirstMistakeDataclass:
    """Smoke checks that FirstMistake stays an immutable, simple
    dataclass."""

    def test_is_frozen(self):
        m = FirstMistake(
            fen_before="dummy",
            played_uci="e2e4",
            move_number=1,
            eval_loss_cp=200,
        )
        import dataclasses

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
