"""Prompt-cache prefix invariants — the layout that makes DeepSeek's
automatic context cache effective.

DeepSeek bills the repeated PREFIX of a request's input at ~1/50th of the
cache-miss rate (verified 2026-07-10; see ``llm/observability.py``'s price
table), with no request opt-in.  That makes the byte-stability of the
prompt's leading segments a real cost property:

  PCP_01  The static segment (rendered header + Mode-2 system prompt +
          app-help guide) is byte-identical across users, positions, and
          conversations — nothing dynamic may creep in front of or inside
          it.  A timestamp, request id, or user name injected early would
          silently re-bill ~3k tokens per call at 50x.
  PCP_02  Within one conversation, the history block extends the shared
          prefix: two renders of the SAME conversation at DIFFERENT
          positions (the ask → move → ask flow) share everything through
          the previously-accumulated history.  This pins the 2026-07-10
          reorder (player context + history BEFORE the per-position
          perspective / facts / style blocks in ``_build_chat_prompt``).
  PCP_03  Mode-1 prompts share the Mode-1 system prompt as their prefix
          across positions.

These are cost invariants, not safety gates — but like the safety pins
they exist so a well-meaning edit can't silently regress the property.
"""

from __future__ import annotations

from os.path import commonprefix

from llm.rag.prompts.app_help import CEREVEON_GUIDE
from llm.rag.prompts.mode_1.render import render_mode_1_prompt
from llm.rag.prompts.system_mode_1 import SYSTEM_PROMPT_MODE_1
from llm.rag.prompts.system_v2_mode_2 import SYSTEM_PROMPT
from llm.seca.coach.chat_pipeline import ChatTurn, _build_chat_prompt

# Two distinct, fully-populated engine signals (same shape the chat
# pipeline receives from ``_chat_engine_signal``).
_ESV_OPENING_EQUAL = {
    "evaluation": {"type": "cp", "band": "equal", "side": "white"},
    "eval_delta": "stable",
    "last_move_quality": "unknown",
    "tactical_flags": [],
    "position_flags": [],
    "phase": "opening",
}
_ESV_MIDDLEGAME_AHEAD = {
    "evaluation": {"type": "cp", "band": "clear_advantage", "side": "black"},
    "eval_delta": "improving",
    "last_move_quality": "good",
    "tactical_flags": [],
    "position_flags": [],
    "phase": "middlegame",
}

_FEN_A = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_FEN_B = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 4 3"

_PROFILE = {
    "skill_estimate": "intermediate",
    "common_mistakes": [{"tag": "hanging pieces"}],
    "strengths": ["endgame patience"],
}


def test_pcp_01_static_prefix_is_byte_identical_across_everything():
    """PCP_01: users with different positions, histories, profiles, voices
    and colours still share the full [header + system prompt + app guide]
    prefix — the segment every single request cache-hits on."""
    prompt_a = _build_chat_prompt(
        _FEN_A,
        [ChatTurn(role="user", content="What should I focus on here?")],
        None,
        _ESV_OPENING_EQUAL,
        coach_voice=None,
        player_color="white",
    )
    prompt_b = _build_chat_prompt(
        _FEN_B,
        [
            ChatTurn(role="user", content="Was that trade good?"),
            ChatTurn(role="assistant", content="It kept your structure intact."),
            ChatTurn(role="user", content="And now?"),
        ],
        _PROFILE,
        _ESV_MIDDLEGAME_AHEAD,
        past_mistakes=["tactical_vision"],
        coach_voice="formal",
        player_color="black",
    )

    common = commonprefix([prompt_a, prompt_b])

    # The chat renderer (llm/rag/prompts/mode_2/render.py) opens with the
    # composed system block directly, so the shared prefix must begin with
    # the system prompt's own first bytes, and the ENTIRE system prompt
    # and ENTIRE guide must sit inside it, unbroken.
    assert common.startswith(SYSTEM_PROMPT.strip()[:100])
    assert SYSTEM_PROMPT.strip() in common, (
        "the Mode-2 system prompt is no longer a shared prompt prefix — "
        "something dynamic was injected before or inside it, which "
        "re-bills the whole segment at the cache-miss rate on every call"
    )
    assert CEREVEON_GUIDE.strip() in common, (
        "the app-help guide is no longer a shared prompt prefix — "
        "see llm/rag/prompts/app_help.py (the guide is deliberately "
        "static and placed right after the system prompt)"
    )


def test_pcp_02_history_extends_the_cached_prefix_within_a_conversation():
    """PCP_02: same conversation, position advanced (ask → move → ask):
    the earlier turns' history must be inside the common prefix, so only
    the per-position tail re-bills at the miss rate."""
    turns_early = [
        ChatTurn(role="user", content="Why is my knight awkward here?"),
        ChatTurn(role="assistant", content="It blocks your own pawn's best plan."),
        ChatTurn(role="user", content="Should I reroute it?"),
    ]
    # Two moves later: same conversation, two more turns, NEW position.
    turns_later = turns_early + [
        ChatTurn(role="assistant", content="Yes — aim it at the weakened dark squares."),
        ChatTurn(role="user", content="Did my last move help with that?"),
    ]

    prompt_early = _build_chat_prompt(
        _FEN_A,
        turns_early,
        _PROFILE,
        _ESV_OPENING_EQUAL,
        past_mistakes=["tactical_vision"],
        coach_voice="conversational",
        player_color="white",
    )
    prompt_later = _build_chat_prompt(
        _FEN_B,
        turns_later,
        _PROFILE,
        _ESV_MIDDLEGAME_AHEAD,
        past_mistakes=["tactical_vision"],
        coach_voice="conversational",
        player_color="white",
    )

    common = commonprefix([prompt_early, prompt_later])

    # Per-user context precedes history and is shared.
    assert "PLAYER CONTEXT:" in common
    # The whole history both renders agree on sits inside the prefix —
    # including the LAST shared line.  If the per-position blocks ever
    # move back in front of history, the divergence point jumps to
    # before "CONVERSATION HISTORY:" and these fail.
    assert "CONVERSATION HISTORY:" in common
    assert "User: Why is my knight awkward here?" in common
    assert "Coach: It blocks your own pawn's best plan." in common
    # Sanity: the prompts DO diverge (different positions) — the property
    # under test is where, not whether.
    assert prompt_early != prompt_later


def test_pcp_03_mode_1_prompts_share_the_system_prompt_prefix():
    """PCP_03: per-move hint prompts across different positions share the
    Mode-1 system prompt as their common prefix."""
    prompt_a = render_mode_1_prompt(
        system_prompt=SYSTEM_PROMPT_MODE_1,
        engine_signal=_ESV_OPENING_EQUAL,
        fen=_FEN_A,
        explanation_style="simple",
        player_color="white",
    )
    prompt_b = render_mode_1_prompt(
        system_prompt=SYSTEM_PROMPT_MODE_1,
        engine_signal=_ESV_MIDDLEGAME_AHEAD,
        fen=_FEN_B,
        explanation_style="advanced",
        player_color="black",
    )

    common = commonprefix([prompt_a, prompt_b])
    assert SYSTEM_PROMPT_MODE_1.strip() in common, (
        "the Mode-1 system prompt is no longer a shared prompt prefix — "
        "dynamic content crept in front of it in render_mode_1_prompt"
    )
