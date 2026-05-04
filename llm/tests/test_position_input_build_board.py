"""
Tests for position_input.build_board and normalize_position.

Critical invariant pinned here:
  When `moves` is non-empty, `build_board` silently ignores the `fen` argument
  and always starts from the standard starting position, applying the move
  sequence from there. A caller that supplies a non-starting FEN together with
  a non-empty moves list will NOT get the expected board.

This behavior is tested explicitly so that any future change to respect `fen`
when moves are present triggers a review — callers in engine_eval.py and
elite_engine_service.py depend on the current semantics.
"""

import chess

from llm.position_input import build_board, normalize_position

_FEN_AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
_FEN_AFTER_E4_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"


# ---------------------------------------------------------------------------
# Basic single-argument behaviour
# ---------------------------------------------------------------------------


def test_build_board_from_fen_only():
    board = build_board(fen=_FEN_AFTER_E4)
    assert board.fen() == _FEN_AFTER_E4


def test_build_board_startpos_alias_resolves_to_starting_fen():
    board = build_board(fen="startpos")
    assert board.fen() == chess.STARTING_FEN


def test_build_board_no_args_returns_starting_position():
    board = build_board()
    assert board.fen() == chess.STARTING_FEN


def test_build_board_none_fen_returns_starting_position():
    board = build_board(fen=None)
    assert board.fen() == chess.STARTING_FEN


def test_build_board_from_moves_only():
    board = build_board(moves=["e2e4", "e7e5"])
    expected = chess.Board()
    expected.push_uci("e2e4")
    expected.push_uci("e7e5")
    assert board.fen() == expected.fen()
    assert board.fen() == _FEN_AFTER_E4_E5


# ---------------------------------------------------------------------------
# Empty / falsy moves fall through to FEN path
# ---------------------------------------------------------------------------


def test_build_board_empty_moves_list_respects_fen():
    board = build_board(fen=_FEN_AFTER_E4, moves=[])
    assert board.fen() == _FEN_AFTER_E4


def test_build_board_none_moves_respects_fen():
    board = build_board(fen=_FEN_AFTER_E4, moves=None)
    assert board.fen() == _FEN_AFTER_E4


def test_build_board_whitespace_only_moves_respects_fen():
    """Moves that normalise to empty strings are filtered out and treated as empty."""
    board = build_board(fen=_FEN_AFTER_E4, moves=["  ", ""])
    assert board.fen() == _FEN_AFTER_E4


# ---------------------------------------------------------------------------
# FEN is silently ignored when moves are non-empty — regression guard
# ---------------------------------------------------------------------------


def test_build_board_ignores_fen_when_moves_are_present():
    """
    When `moves` is non-empty, `build_board` discards `fen` and builds the
    board from the standard starting position + the move sequence.

    This means a caller passing a non-starting FEN *and* a non-empty moves
    list gets a board rooted at the starting position, not at the supplied FEN.
    The supplied FEN is silently ignored.

    This test pins that existing behaviour. If you change build_board to
    respect `fen` when moves are present, you must audit all callers
    (engine_eval.py, elite_engine_service.py, server.py) for correctness.
    """
    non_starting_fen = _FEN_AFTER_E4  # position after 1. e4
    board = build_board(fen=non_starting_fen, moves=["e2e4"])

    # The FEN is ignored; board reflects startpos + e2e4, not after_e4 + e2e4.
    expected = chess.Board()
    expected.push_uci("e2e4")
    assert board.fen() == expected.fen(), (
        "build_board must start from the standard position when moves are given, "
        "ignoring the supplied fen. Update this test only after auditing all callers."
    )


def test_build_board_fen_after_moves_reflects_move_sequence_not_input_fen():
    """Confirm the resulting FEN matches the move sequence, not the input FEN."""
    board = build_board(fen=_FEN_AFTER_E4_E5, moves=["d2d4"])
    expected = chess.Board()
    expected.push_uci("d2d4")
    # Result is startpos + d4, not after_e4_e5 + d4
    assert board.fen() == expected.fen()
    assert board.fen() != _FEN_AFTER_E4_E5


# ---------------------------------------------------------------------------
# normalize_position — returned FEN reflects actual board state
# ---------------------------------------------------------------------------


def test_normalize_position_returns_board_fen_not_input_fen():
    """
    When moves are present, normalize_position returns the FEN of the board
    built from startpos + moves, not the input fen parameter.

    Uses _FEN_AFTER_E4_E5 as the ignored input FEN and "d2d4" as the move,
    which from starting position yields the d4-opening position — clearly
    different from _FEN_AFTER_E4_E5.
    """
    result_fen, moves_out, board = normalize_position(fen=_FEN_AFTER_E4_E5, moves=["d2d4"])
    expected = chess.Board()
    expected.push_uci("d2d4")
    expected_fen = expected.fen()  # startpos + d4, not _FEN_AFTER_E4_E5

    assert (
        result_fen == expected_fen
    ), "normalize_position must return the FEN of startpos+moves, not the input fen"
    assert (
        result_fen != _FEN_AFTER_E4_E5
    ), "The returned FEN must differ from the (ignored) input FEN"
    assert moves_out == ["d2d4"]
    assert board.fen() == result_fen


def test_normalize_position_with_fen_only_returns_input_fen():
    result_fen, moves_out, board = normalize_position(fen=_FEN_AFTER_E4)
    assert result_fen == _FEN_AFTER_E4
    assert moves_out == []
    assert board.fen() == _FEN_AFTER_E4


def test_normalize_position_startpos_alias():
    result_fen, moves_out, board = normalize_position(fen="startpos")
    assert result_fen == chess.STARTING_FEN
    assert moves_out == []


def test_normalize_position_filters_empty_move_strings():
    result_fen, moves_out, board = normalize_position(
        fen=_FEN_AFTER_E4, moves=["", " ", None]  # type: ignore[list-item]
    )
    assert moves_out == []
    assert result_fen == _FEN_AFTER_E4
