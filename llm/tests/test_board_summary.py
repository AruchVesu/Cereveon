"""Tests for describe_position — deterministic FEN -> piece-list grounding.

Guards Bug 2: the coach used to receive only the raw FEN and would
misread which piece moved (it called the f-pawn the "king's pawn").  The
grounding helper must surface piece placement unambiguously.
"""

from llm.rag.prompts.board_summary import describe_position


def test_startpos_lists_side_to_move_and_both_kings():
    desc = describe_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    assert "Side to move: White." in desc
    assert "White:" in desc and "Black:" in desc
    assert "King e1" in desc
    assert "King e8" in desc


def test_distinguishes_f_pawn_from_e_pawn_after_f3():
    # The exact in-app bug: after the player moved the f-pawn, the coach said
    # "king's pawn".  The summary must show the f-pawn on f3 and the e-pawn
    # still home on e2 so the LLM cannot conflate them.
    fen = "rnbqkbnr/pppppppp/8/8/8/5P2/PPPPP1PP/RNBQKBNR b KQkq - 0 1"
    desc = describe_position(fen)
    assert "f3" in desc  # f-pawn advanced
    assert "e2" in desc  # king's pawn still home
    assert "Side to move: Black." in desc


def test_startpos_sentinel_is_accepted():
    assert "Side to move: White." in describe_position("startpos")


def test_unparseable_or_empty_fen_returns_empty_string():
    assert describe_position("not a fen") == ""
    assert describe_position("") == ""
    assert describe_position(None) == ""


def test_deterministic_for_identical_input():
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    assert describe_position(fen) == describe_position(fen)
