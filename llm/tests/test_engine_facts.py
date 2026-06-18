"""Tests for engine_facts — plain-English, coordinate-free engine grounding.

Stops the chat coach hallucinating tactics by feeding it the real deterministic
board facts.  Every produced string must stay coordinate-free so it can't trip
the no-notation output validator.
"""

import re

import chess
import pytest

from llm.rag.prompts.engine_facts import (
    _eval_fact,
    _FLAG_FACT,
    describe_threats,
    render_engine_facts,
)
from llm.rag.validators._rules import MOVE_ALGEBRAIC_PATTERNS

_NOTATION = [re.compile(p) for p in MOVE_ALGEBRAIC_PATTERNS]


def _fen(moves):
    b = chess.Board()
    for u in moves:
        b.push(chess.Move.from_uci(u))
    return b.fen()


# Position after 1.e4 e5 2.Nf3 Nc6 3.Bc4 Nf6 4.Ng5 — the probe's #7 case.
_NG5 = _fen(["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "f3g5"])


def test_render_facts_player_perspective():
    facts = render_engine_facts(
        {
            "tactical_flags": ["hanging_piece:black"],
            "position_flags": ["king_safety:black_exposed", "material:white_up_piece"],
        }
    )
    assert "Your opponent has an undefended piece under attack." in facts
    assert "Your opponent's king is exposed." in facts
    assert "You are up a piece." in facts


def test_render_facts_empty_and_dedup():
    assert render_engine_facts({}) == []
    assert render_engine_facts({"tactical_flags": [], "position_flags": []}) == []
    deduped = render_engine_facts(
        {"tactical_flags": ["hanging_piece:black", "hanging_piece:black"], "position_flags": []}
    )
    assert deduped == ["Your opponent has an undefended piece under attack."]


def test_eval_band_leads_the_facts():
    facts = render_engine_facts(
        {"evaluation": {"type": "cp", "band": "clear_advantage", "side": "white"}}
    )
    assert facts[0] == "The engine gives you a clear advantage."


def test_eval_mate_for_opponent():
    assert (
        _eval_fact({"type": "mate", "side": "black"})
        == "The engine sees a forced checkmate for your opponent."
    )


def test_eval_equal():
    assert (
        _eval_fact({"type": "cp", "band": "equal", "side": "black"})
        == "The engine evaluates the position as roughly equal."
    )


def test_eval_unknown_returns_empty():
    assert _eval_fact({}) == ""
    assert _eval_fact({"type": "cp", "band": "small_advantage", "side": "unknown"}) == ""


def test_describe_threats_names_the_attacked_pawn_near_the_king():
    desc = describe_threats(_NG5, "f3g5")
    assert "knight" in desc
    assert "king's bishop pawn" in desc  # the f7 pawn the knight now hits
    assert "around the opponent's king" in desc


def test_describe_threats_quiet_move_returns_empty():
    # 1.Nf3 from the start attacks no enemy piece and isn't near the black king.
    assert describe_threats(_fen(["g1f3"]), "g1f3") == ""


@pytest.mark.parametrize("fen,uci", [("", "f3g5"), (_NG5, ""), ("not a fen", "f3g5")])
def test_describe_threats_unparseable_returns_empty(fen, uci):
    assert describe_threats(fen, uci) == ""


def test_no_fact_or_threat_contains_chess_notation():
    eval_samples = [
        _eval_fact({"type": "mate", "side": "white"}),
        _eval_fact({"type": "mate", "side": "black"}),
        _eval_fact({"type": "cp", "band": "equal", "side": "black"}),
        _eval_fact({"type": "cp", "band": "decisive_advantage", "side": "white"}),
        _eval_fact({"type": "cp", "band": "small_advantage", "side": "black"}),
    ]
    samples = (
        list(_FLAG_FACT.values())
        + eval_samples
        + [
            describe_threats(_NG5, "f3g5"),
            describe_threats(_fen(["e2e4", "d7d5", "e4d5"]), "e4d5"),
        ]
    )
    for text in samples:
        for pattern in _NOTATION:
            assert re.search(pattern, text) is None, f"{text!r} matches forbidden {pattern!r}"
