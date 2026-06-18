"""Tests for describe_move_plain — coordinate-free move grounding.

Guards Bug 2: the coach must name the moved piece in plain English ("f-pawn")
so it reads accurately, AND the phrase must never contain a <file><rank> token
(e.g. "f3") — otherwise the no-notation output validator would strip it and the
coach would fall back to the canned line (the #247/#248 regression).
"""

import re

import pytest

from llm.rag.prompts.move_phrase import describe_move_plain
from llm.rag.validators._rules import MOVE_ALGEBRAIC_PATTERNS

# Position AFTER each move, with the UCI that produced it.
_F3 = "rnbqkbnr/pppppppp/8/8/8/5P2/PPPPP1PP/RNBQKBNR b KQkq - 0 1"
_F4 = "rnbqkbnr/pppppppp/8/8/5P2/8/PPPPP1PP/RNBQKBNR b KQkq f3 0 1"
_NF3 = "rnbqkbnr/pppppppp/8/8/8/5N2/PPPPPPPP/RNBQKB1R b KQkq - 1 1"
_EXD5 = "rnbqkbnr/ppp1pppp/8/3P4/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2"
_OO = "rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQ1RK1 b kq - 5 4"
_PROMO = "Q7/8/8/8/8/8/8/k1K5 b - - 0 1"

_ALL_PHRASES = [
    describe_move_plain(_F3, "f2f3"),
    describe_move_plain(_F4, "f2f4"),
    describe_move_plain(_NF3, "g1f3"),
    describe_move_plain(_EXD5, "e4d5"),
    describe_move_plain(_OO, "e1g1"),
    describe_move_plain(_PROMO, "a7a8q"),
]


def test_pawn_single_advance_names_the_file_pawn():
    # The exact in-app bug: f2-f3 must be "the f-pawn", not the king's pawn.
    assert describe_move_plain(_F3, "f2f3") == "advanced the f-pawn one square"


def test_pawn_double_advance():
    assert describe_move_plain(_F4, "f2f4") == "advanced the f-pawn two squares"


def test_piece_move_names_the_piece_type():
    assert describe_move_plain(_NF3, "g1f3") == "moved a knight"


def test_pawn_capture_uses_origin_file():
    assert describe_move_plain(_EXD5, "e4d5") == "captured with the e-pawn"


def test_castling_kingside():
    assert describe_move_plain(_OO, "e1g1") == "castled kingside"


def test_promotion():
    assert describe_move_plain(_PROMO, "a7a8q") == "promoted the a-pawn to a queen"


@pytest.mark.parametrize("fen,uci", [("", "f2f3"), (_F3, ""), (_F3, "zz"), ("not a fen", "f2f3")])
def test_unparseable_returns_empty(fen, uci):
    assert describe_move_plain(fen, uci) == ""


@pytest.mark.parametrize("phrase", _ALL_PHRASES)
def test_phrases_never_contain_chess_notation(phrase):
    # The whole point: no <file><rank> / castling token the output validator
    # would strip.  If this fails, the grounding would re-trigger the fallback.
    assert phrase  # all sample moves produce a phrase
    for pattern in MOVE_ALGEBRAIC_PATTERNS:
        assert re.search(pattern, phrase) is None, f"{phrase!r} matches forbidden {pattern!r}"
