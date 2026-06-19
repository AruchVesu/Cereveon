"""Tests for move_quality — centipawn-loss move classification for Mode-1.

Pins the eval->player-cp conversion (cp + mate, both perspectives) and the
loss-band classification, including the cp<->mate transitions that must read as
"best" (set up a mate) or "blunder" (throw one away / walk into one).
"""

from llm.rag.engine_signal.move_quality import (
    _MATE_CP,
    classify_move_quality,
    eval_to_player_cp,
)


class TestEvalToPlayerCp:
    def test_cp_white_perspective(self):
        assert eval_to_player_cp({"type": "cp", "value": 120}, True) == 120
        assert eval_to_player_cp({"type": "cp", "value": 120}, False) == -120

    def test_cp_negative_value(self):
        assert eval_to_player_cp({"type": "cp", "value": -80}, True) == -80
        assert eval_to_player_cp({"type": "cp", "value": -80}, False) == 80

    def test_mate_for_white(self):
        assert eval_to_player_cp({"type": "mate", "value": 3}, True) == _MATE_CP - 3
        assert eval_to_player_cp({"type": "mate", "value": 3}, False) == -(_MATE_CP - 3)

    def test_mate_for_black(self):
        # value < 0 = Black mates; bad for White, good for Black.
        assert eval_to_player_cp({"type": "mate", "value": -2}, True) == -_MATE_CP + 2
        assert eval_to_player_cp({"type": "mate", "value": -2}, False) == _MATE_CP - 2

    def test_closer_mate_has_larger_magnitude(self):
        assert eval_to_player_cp({"type": "mate", "value": 1}, True) > eval_to_player_cp(
            {"type": "mate", "value": 5}, True
        )
        assert eval_to_player_cp({"type": "mate", "value": -1}, True) < eval_to_player_cp(
            {"type": "mate", "value": -5}, True
        )

    def test_terminal_mate_zero_is_neutral(self):
        # mate:0 (terminal checkmate) winner is not determinable here; neutral.
        assert eval_to_player_cp({"type": "mate", "value": 0}, True) == 0

    def test_malformed_eval_is_zero(self):
        assert eval_to_player_cp({}, True) == 0
        assert eval_to_player_cp({"type": "cp", "value": "x"}, True) == 0
        assert eval_to_player_cp("not a dict", True) == 0  # type: ignore[arg-type]


class TestClassifyMoveQuality:
    def test_best_when_no_loss(self):
        assert classify_move_quality(50, 50) == "best"
        assert classify_move_quality(50, 35) == "best"  # loss 15 <= 20

    def test_best_when_move_improves_eval(self):
        # Negative loss (eval went up vs prior best-play estimate) clamps to best.
        assert classify_move_quality(10, 60) == "best"

    def test_good(self):
        assert classify_move_quality(100, 50) == "good"  # loss 50

    def test_inaccuracy(self):
        assert classify_move_quality(100, 0) == "inaccuracy"  # loss 100

    def test_mistake(self):
        assert classify_move_quality(100, -150) == "mistake"  # loss 250

    def test_blunder(self):
        assert classify_move_quality(100, -300) == "blunder"  # loss 400

    def test_throwing_away_a_forced_mate_is_a_blunder(self):
        before = eval_to_player_cp({"type": "mate", "value": 2}, True)
        after = eval_to_player_cp({"type": "cp", "value": 50}, True)
        assert classify_move_quality(before, after) == "blunder"

    def test_setting_up_a_forced_mate_is_best(self):
        before = eval_to_player_cp({"type": "cp", "value": 400}, True)
        after = eval_to_player_cp({"type": "mate", "value": 3}, True)
        assert classify_move_quality(before, after) == "best"

    def test_walking_into_a_mate_is_a_blunder(self):
        before = eval_to_player_cp({"type": "cp", "value": 30}, True)
        after = eval_to_player_cp({"type": "mate", "value": -2}, True)
        assert classify_move_quality(before, after) == "blunder"
