"""Cereveon app-help layer — detector, injection, and guide-safety pins.

The Mode-2 coach's second knowledge domain (see
``llm/rag/prompts/app_help.py``).  These deterministic tests guard the
three properties that keep the feature safe and cheap:

1. Detection precision/recall — app-noun turns inject the guide; ordinary
   chess-coaching phrasing does NOT (so pure-chess turns stay
   byte-identical and the REQUIRE-gate compliance is untouched).
2. Injection wiring — the guide reaches the assembled Mode-2 prompt on an
   app turn and is absent on a chess turn, on the real
   ``_build_chat_prompt`` path shared by /chat and /chat/stream.
3. Guide safety — the guide text seeds no forbidden OUTPUT (the model may
   echo it, and that echo must pass the Mode-2 validators) and claims no
   feature the app does not actually implement.
"""

from __future__ import annotations

import re

import pytest

from llm.rag.prompts.app_help import (
    CEREVEON_GUIDE,
    build_app_help_block,
    is_app_help_query,
)
from llm.seca.coach.chat_pipeline import ChatTurn, _build_chat_prompt

# Real, distinct app questions — each names an app concept.
_APP_QUERIES = [
    "How do I import my lichess games?",
    "What does terse coach voice do?",
    "How do I see my past games?",
    "How do I upgrade to Cereveon Pro?",
    "Where are the settings?",
    "How do I start a daily drill?",
    "What can you do?",
    "How do I resume my game?",
    "How do I change the board style?",
    "How do I get started?",
]

# Ordinary chess-coaching turns — must NOT trip app detection even though
# some share verbs ("use", "review", "improve", "how do I") with app help.
_CHESS_QUERIES = [
    "How do I improve my endgames?",
    "Should I use my bishop pair?",
    "How important is the center?",
    "What is my plan here?",
    "How do I attack the king?",
    "Is this position winning?",
    "Review my last move please.",
    "What went wrong with my opening?",
    "How do I make progress in this position?",
    "What should I focus on?",
]


@pytest.mark.parametrize("q", _APP_QUERIES)
def test_app_queries_detected(q: str) -> None:
    assert is_app_help_query(q), f"app question not detected: {q!r}"


@pytest.mark.parametrize("q", _CHESS_QUERIES)
def test_chess_queries_not_detected(q: str) -> None:
    assert not is_app_help_query(q), f"chess question wrongly detected as app help: {q!r}"


def test_import_does_not_false_match_important() -> None:
    # "import" ⊂ "important" — the classic collision the token set avoids.
    assert not is_app_help_query("How important is king safety?")
    assert is_app_help_query("How do I import my games?")


def test_block_is_conditional() -> None:
    assert build_app_help_block("How do I attack the king?") == ""
    block = build_app_help_block("How do I import my lichess games?")
    assert "CEREVEON APP GUIDE" in block
    # The anti-invention contract + the shareable-not-hidden framing must
    # both be present (they are what keep the feature safe on a false
    # positive and un-refused under the prompt-secrecy rule).
    assert "never invent" in block.lower()
    assert "share" in block.lower()
    assert "ignore this guide" in block.lower()


# ---------------------------------------------------------------------------
# Injection wiring — the real Mode-2 prompt assembly.
# ---------------------------------------------------------------------------

_ESV = {
    "evaluation": {"type": "cp", "band": "equal", "side": "white"},
    "eval_delta": "stable",
    "last_move_quality": "unknown",
    "tactical_flags": [],
    "position_flags": [],
    "phase": "opening",
}
_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _prompt_for(query: str) -> str:
    return _build_chat_prompt(
        _FEN,
        [ChatTurn(role="user", content=query)],
        None,
        _ESV,
    )


# The guide's heading is unique to the injected BLOCK.  ("CEREVEON APP
# GUIDE" alone is NOT a valid marker: the system prompt's rule 8 now names
# it too, so it appears on every turn — the block presence must be keyed on
# the guide body, which only the injection adds.)
_GUIDE_HEADING = "CEREVEON — WHAT IT DOES AND HOW TO USE IT"


def test_guide_injected_on_app_turn() -> None:
    prompt = _prompt_for("How do I import my lichess games?")
    assert _GUIDE_HEADING in prompt
    assert "Import your Lichess games" in prompt


def test_guide_absent_on_chess_turn() -> None:
    prompt = _prompt_for("How do I attack the king?")
    assert _GUIDE_HEADING not in prompt
    assert "Import your Lichess games" not in prompt


def test_chess_turn_prompt_unchanged_by_feature() -> None:
    # The whole economics argument: a pure-chess turn must be byte-identical
    # to what it would be with no app-help feature at all — i.e. adding the
    # guide block is a no-op string on chess turns.
    from llm.rag.prompts import app_help

    with_feature = _prompt_for("What is my plan in this position?")
    # Simulate "feature absent" by forcing the block empty and rebuilding.
    orig = app_help.build_app_help_block
    try:
        app_help.build_app_help_block = lambda *_a, **_k: ""
        import llm.seca.coach.chat_pipeline as cp

        cp.build_app_help_block = lambda *_a, **_k: ""
        without_feature = _prompt_for("What is my plan in this position?")
    finally:
        app_help.build_app_help_block = orig
        import llm.seca.coach.chat_pipeline as cp

        cp.build_app_help_block = orig
    assert with_feature == without_feature


# ---------------------------------------------------------------------------
# Guide safety — the text may be echoed into OUTPUT, so it must not seed a
# validator violation, must not reveal the coach's engine, and must not
# claim a feature the app does not implement.
# ---------------------------------------------------------------------------


def test_guide_seeds_no_forbidden_output() -> None:
    lower = CEREVEON_GUIDE.lower()
    # Engine secrecy (rule 7 / THREAT_MODEL): the coach never reveals it
    # uses an engine.  The OPPONENT is openly a computer, so the guide
    # says "computer opponent" and never the word "engine" / "stockfish".
    assert "stockfish" not in lower
    assert "engine" not in lower
    # No mate-claim / notation vocabulary the Mode-2 gates forbid.
    assert "checkmate" not in lower
    assert not re.search(r"\b[KQRBN]?[a-h][1-8]\b", CEREVEON_GUIDE), "square/move notation in guide"
    assert not re.search(r"\b[a-h]-(?:file|pawn)\b", lower), "file-letter reference in guide"


def test_guide_grounds_absent_features_honestly() -> None:
    lower = CEREVEON_GUIDE.lower()
    # The guide must give the model a POSITIVE fact to cite for the features
    # users plausibly expect but Cereveon lacks — an openings trainer,
    # online play, tournaments, sound.  (Live testing showed a bare
    # "don't invent" instruction wasn't enough; the model filled the vacuum
    # from world knowledge.  Grounding the "no" in the guide fixes it.)
    assert "does not have" in lower or "not have" in lower
    assert "no separate openings" in lower
    assert "cannot play online" in lower
    assert "no tournaments" in lower and "no move sound" in lower
    # ...but it must NOT give positive how-to navigation for any of them.
    for positive in (
        "openings tab",
        "opening trainer helps",
        "tap openings",
        "access the openings",
        "enable sound",
        "turn on sound",
        "start a tournament",
        "join a tournament",
    ):
        assert positive not in lower, f"guide gives positive how-to for an absent feature: {positive!r}"


def test_guide_states_verified_pricing_and_limits() -> None:
    # Pinned to the code-verified values (entitlements.service LIMITS +
    # PaywallActivity DEFAULT_PLANS) so a guide edit can't silently drift
    # from what the app actually enforces / charges.
    assert "€9.99" in CEREVEON_GUIDE and "€71.99" in CEREVEON_GUIDE
    assert "one fully coached game" in CEREVEON_GUIDE
