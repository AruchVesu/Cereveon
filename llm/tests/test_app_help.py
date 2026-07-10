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
    "How do the puzzles work?",
    "Where can I practise puzzles?",
    "What can you do?",
    "How do I resume my game?",
    "How do I change the board style?",
    "How do I get started?",
    # Natural phrasings without an app noun — must still detect (the recall
    # fix from the 2026-07-08 10-example live test).
    "Can I look back at the games I played earlier?",
    "How do I change the way the board looks?",
    "How do I get back to a game I didn't finish?",
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
    # Precision guards: "board" / "games" in their CHESS sense must NOT trip
    # the natural-phrase tokens ("change the board" / "how the board looks"
    # / "games i played").
    "How do I control the centre of the board?",
    "Is the whole board balanced right now?",
    "What can I learn from the games of the great attackers?",
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


def test_block_always_built_and_framed() -> None:
    # Always-on (no gating): the builder takes no query and always returns
    # the framed guide.  The three framing properties that make it safe:
    block = build_app_help_block()
    assert _GUIDE_HEADING in block
    # never refuses an app question with the forbidden phrase,
    assert 'must not reply "i can only help with chess"' in block.lower()
    # never invents an absent feature,
    assert "never invent" in block.lower()
    # and is inert on a chess turn.
    assert "ignore this guide" in block.lower()
    assert "share" in block.lower()
    # Deterministic / constant — identical every call (so it caches).
    assert build_app_help_block() == block


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


def test_guide_present_on_app_turn() -> None:
    prompt = _prompt_for("How do I import my lichess games?")
    assert _GUIDE_HEADING in prompt
    assert "Import your Lichess games" in prompt


def test_guide_present_but_framed_inert_on_chess_turn() -> None:
    # Always-on: the guide is in the prompt on a chess turn too, but the
    # framing tells the model to IGNORE it and coach the position (verified
    # live: chess turns are coached normally, the guide is inert).  This is
    # the tradeoff that guarantees an app question can never miss the guide
    # and fall to the "I can only help with chess" refusal.
    prompt = _prompt_for("How do I attack the king?")
    assert _GUIDE_HEADING in prompt
    assert "ignore this guide" in prompt.lower()


def test_anti_refusal_reminder_is_last_and_forbids_both_refusals() -> None:
    # The full guide is early (cacheable); a short reminder is appended
    # AFTER the per-turn context so the anti-refusal has recency over
    # rule 9's "not enough information" — the fix for the 4/10 app
    # misfire on the first always-on run.
    prompt = _prompt_for("What does the terse coach voice do?")
    low = prompt.lower()
    assert "reminder" in low
    # forbids BOTH canned position-refusals for an app question, and
    # demands the first sentence actually answer it,
    assert "i can only help with chess" in low
    assert "there is not enough information" in low
    assert "first sentence" in low
    # Appended at the END of the system block (after the guide + the
    # per-turn context) — the same end-of-system recency slot the terse
    # reminder uses.  render_mode_2 then appends the FEN + user query, so
    # the reminder precedes the FEN; what matters is it comes AFTER the
    # guide and the position facts, not before the rendered board.
    assert prompt.rindex("REMINDER") > prompt.index(_GUIDE_HEADING)


def test_spurious_refusal_backstop_predicate() -> None:
    # The backstop fires on an OBJECTIVELY spurious refusal (no keyword
    # detection — that inherited the recall hole).  "only help with chess"
    # is never valid; "not enough information" is spurious iff the signal
    # is populated (always true in the chat path).
    from llm.seca.coach.chat_pipeline import _is_spurious_refusal

    populated = {"evaluation": {"type": "cp", "band": "equal", "side": "white"}}
    empty = {}  # unparseable FEN → no evaluation

    # "only help with chess" is always spurious — even the natural-phrased
    # app question the detector would MISS still gets caught.
    assert _is_spurious_refusal("I can only help with chess.", populated)
    assert _is_spurious_refusal("I can only help with chess.", empty)
    # "not enough information" with a populated signal is spurious...
    assert _is_spurious_refusal(
        "There is not enough information to assess this position.", populated
    )
    # ...but LEGITIMATE when the signal is genuinely empty (rule 9).
    assert not _is_spurious_refusal(
        "There is not enough information to assess this position.", empty
    )
    # a real answer is never a refusal.
    assert not _is_spurious_refusal("Open Settings and pick a board style.", populated)


def test_guide_sits_in_the_cacheable_prefix() -> None:
    # The block is placed right after the static system prompt (before the
    # per-turn voice / perspective / position content), so [system + guide]
    # is a static prefix that prompt-caches and the mate-critical content
    # keeps recency after it.  Assert the guide appears before the FEN /
    # engine-signal section that render_mode_2 emits.
    prompt = _prompt_for("What is my plan in this position?")
    assert _GUIDE_HEADING in prompt
    # The rendered FEN appears only in render_mode_2's tail (the system
    # prompt refers to "the FEN" but never embeds the board string), so it
    # is a clean marker for "the per-turn position content".
    assert prompt.index(_GUIDE_HEADING) < prompt.index(_FEN)


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


def test_guide_matches_current_navigation() -> None:
    # Nav was simplified to Home · Puzzles · You (PRs #379/#381/#382,
    # 2026-07): Lessons and Coach tabs retired, Puzzles tab added,
    # header avatar opens Settings.  The guide went stale once (it
    # described the four-tab layout until 2026-07-10) — a coach telling
    # users to tap a tab that no longer exists is an invented feature,
    # exactly what this layer forbids.  Pin the CURRENT layout so the
    # next nav change breaks this test instead of the guide.
    lower = CEREVEON_GUIDE.lower()
    assert "three tabs" in lower
    assert '"puzzles"' in lower, "guide must document the Puzzles tab"
    assert '"you" tab' in lower, "guide must document the You tab"
    assert "avatar" in lower, "guide must name the avatar → Settings entry"
    # Retired surfaces must not be navigable-to any more.
    assert "lessons" not in lower, "Lessons tab was retired in PR #379"
    assert '"coach" tab' not in lower, "Coach tab was retired in PR #379"
    assert "four tabs" not in lower
