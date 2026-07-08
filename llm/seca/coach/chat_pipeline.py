"""
Long-form chat coaching pipeline — LLM-powered with deterministic fallback.

Architecture
------------
Inputs:
    fen            Current board position (FEN string).
    messages       List of ChatTurn(role, content) — full conversation history
                   including the latest user message.
    player_profile Optional player context (skill_estimate, common_mistakes,
                   strengths) from the SECA player model.
    past_mistakes  Optional list of MistakeCategory strings from the analytics
                   layer (e.g. ["tactical_vision", "endgame_technique"]).

Processing:
    1. Extract engine signal from the FEN via extract_engine_signal().
    2. Try LLM path:
       a. Sanitize latest user query.
       b. Build Mode-2 prompt with conversation history, RAG docs, player
          context, and engine signal.
       c. Call Ollama; validate and repair output.
       d. Return ChatReply with LLM-generated explanation.
    3. On any LLM failure, fall back to the deterministic _build_reply().
    4. Return ChatReply(reply, engine_signal, mode="CHAT_V1").

Constraints
-----------
- No reinforcement learning.
- No dynamic skill adaptation.
- engine_signal is always produced by extract_engine_signal(), never
  sourced from LLM or user input.
- LLM output is validated by validate_mode_2_negative before returning.
- Deterministic fallback always available when LLM is unreachable.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import chess

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.prompts.app_help import (
    APP_HELP_REMINDER,
    build_app_help_block,
    is_app_help_query,
)
from llm.rag.prompts.engine_facts import describe_threats, render_engine_facts
from llm.rag.prompts.move_phrase import describe_move_plain
from llm.seca.coach.context_compact import compact_history, should_compact
from llm.seca.engines.stockfish.board_features import (
    compute_position_flags,
    compute_tactical_flags,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional LLM imports
# ---------------------------------------------------------------------------

# Pre-LLM injection sanitizer.  Pulled out of the LLM-availability guard
# below because ``_safe_sanitize`` (used by the deterministic fallback
# path too — F-09 fix) calls it; the deterministic path must be able to
# drop injection-laced nested player-profile leaves whether or not the
# rest of the LLM toolkit imports cleanly.  ``input_sanitizer`` is pure-
# regex / Python stdlib with no external deps.
from llm.rag.prompts.input_sanitizer import sanitize_user_query as _sanitize  # noqa: E402

try:
    from llm.seca.coach.explain_pipeline import call_llm as _call_llm  # type: ignore[import]
    from llm.rag.prompts.system_v2_mode_2 import SYSTEM_PROMPT as _SYSTEM_PROMPT  # type: ignore[import]
    from llm.rag.prompts.mode_2.render import render_mode_2_prompt as _render  # type: ignore[import]
    from llm.rag.retriever.retriever import retrieve as _retrieve  # type: ignore[import]
    from llm.rag.documents import ALL_RAG_DOCUMENTS as _DOCS  # type: ignore[import]
    from llm.seca.coach.confidence_language_controller import build_language_controller_block as _build_clc  # type: ignore[import]
    # Mode-2 boundary gates run via the shared helper.  See
    # ``llm/seca/coach/_mode_2_validators.py`` for the parity invariant
    # and exception lineage.  These exception types are still imported
    # locally because the calling retry loop catches them separately —
    # firewall-class is "no retry, deterministic"; AssertionError /
    # Mode2Violation are "retry once with hint, then deterministic".
    from llm.seca.coach._mode_2_validators import validate_mode_2_or_raise  # type: ignore[import]
    from llm.rag.validators.mode_2_semantic import Mode2Violation as _Mode2Violation  # type: ignore[import]
    from llm.rag.safety.output_firewall import OutputFirewallError as _OutputFirewallError  # type: ignore[import]
    from llm.rag.validators.explain_response_schema import EngineSignalSchema as _EngineSignalSchema  # type: ignore[import]
    _LLM_AVAILABLE = True
except Exception as _llm_import_exc:  # noqa: BLE001
    logger.warning("LLM imports unavailable — deterministic path only: %s", _llm_import_exc)
    _LLM_AVAILABLE = False

#: Retry budget — sourced from ``llm.rag.llm.config.MAX_MODE_2_RETRIES``
#: so the four LLM-bearing pipelines stay in lock-step.  PR 11
#: (2026-05-15) consolidated.  Local alias preserved so a reader
#: grepping for "chat retry budget" lands on this module.
from llm.rag.llm.config import MAX_MODE_2_RETRIES as _CONFIG_MAX_RETRIES

_CHAT_MAX_RETRIES = _CONFIG_MAX_RETRIES
_CHAT_RETRY_DELAY_SECONDS = 0.5
_CHAT_RETRY_HINT = (
    "\n\nIMPORTANT: Follow MODE-2 rules strictly. "
    "Do NOT speculate, invent moves, or mention engine intentions."
)

# Deterministic backstop for the app-help feature: the LLM occasionally
# leads with a canned position-refusal ("There is not enough information
# ...", "I can only help with chess") despite the always-on guide, on an
# app question OR (rarely) a chess one.  A refusal to a clear "how do I
# use Cereveon?" question is not "acting properly", so the retry loop
# treats a SPURIOUS refusal as a soft failure and re-asks with this hint.
#
# The trigger is OBJECTIVE, not keyword detection (an earlier version
# gated on ``is_app_help_query`` and inherited its recall hole — a
# natural-phrased app question that missed the tokens got no backstop and
# the refusal shipped, 2/10 on a fresh live test).  Instead:
#   * "I can only help with chess" is ALWAYS spurious — the coach helps
#     with chess AND the app, so this exact phrase is never correct.
#   * "not enough information to assess this position" is legitimate ONLY
#     when the engine signal is genuinely empty (rule 9 / an unparseable
#     FEN).  In the chat path the signal is always populated (material
#     fallback), so the refusal is spurious there regardless of how the
#     question was phrased — no detector, no recall gap.
# The neutral hint fixes both a mis-refused app question (answer from the
# guide) and a mis-refused chess one (assess from the signal).
_SPURIOUS_RETRY_HINT = (
    "\n\nYou HAVE the analysis for this position.  Do NOT reply that there "
    "is not enough information, and do NOT say you can only help with "
    "chess.  If the player's message is about the chess position, assess "
    "it from the engine signal.  If it is about using the Cereveon app, "
    "answer it directly from the CEREVEON APP GUIDE above — name the tab "
    "or Settings row and the steps."
)
_ONLY_CHESS_RE = re.compile(r"i can only help with chess", re.IGNORECASE)
_NOT_ENOUGH_RE = re.compile(
    r"not enough information to assess this position", re.IGNORECASE
)


def _is_spurious_refusal(reply: str, engine_signal: dict) -> bool:
    """True when the reply is a refusal that cannot be correct here.

    ``i can only help with chess`` is never correct (chess + app scope).
    ``not enough information`` is correct only on an empty signal; a
    populated signal (always the case in the chat path) makes it spurious.
    """
    if _ONLY_CHESS_RE.search(reply):
        return True
    if _NOT_ENOUGH_RE.search(reply):
        return bool(engine_signal.get("evaluation"))
    return False


# Targeted retry hints — the generic ``_CHAT_RETRY_HINT`` told the model
# to "follow the rules" but not WHICH word tripped, so it re-failed the
# same way and exhausted to the robotic deterministic fallback.  A common
# case: explaining a general opening idea ("how do I play a gambit?") the
# model writes "a winning attack" or a bare "sacrifice", which the
# position-grounded semantic gate rejects on a level / quiet board even
# though the words describe the CONCEPT, not the position.  Naming the
# exact word and giving a natural rephrase lets the model recover on the
# next attempt instead of falling back.  This does NOT weaken any
# validator — it helps the model produce a reply that PASSES them.
_RETRY_KEEP_NATURAL = "  Keep your answer warm and natural — do not become terse or robotic."

_EQUAL_ADV_RE = re.compile(r"described as advantage: '([^']+)'")
_INVENTED_TACTIC_RE = re.compile(r"[Ii]nvented tactic without flag: '([^']+)'")
_SPECULATIVE_RE = re.compile(r"[Ss]peculative language detected: '([^']+)'")
_FORBIDDEN_PATTERN_RE = re.compile(r"pattern `([^`]+)`|forbidden section `([^`]+)`")


def _targeted_retry_hint(exc: Exception) -> str:
    """Turn a validator exception into a specific, natural rephrase hint."""
    msg = str(exc)

    m = _EQUAL_ADV_RE.search(msg)
    if m:
        word = m.group(1)
        return (
            f"\n\nIMPORTANT: you used \"{word}\", but the CURRENT position is "
            f"level — do not call it {word}.  If you are explaining a general "
            f"idea (like how a gambit works), describe it as a concept — e.g. "
            f"\"a strong attack\", \"active, aggressive play\", \"the initiative\" "
            f"— rather than saying the position in front of the player is "
            f"{word}." + _RETRY_KEEP_NATURAL
        )

    m = _INVENTED_TACTIC_RE.search(msg)
    if m:
        word = m.group(1)
        # Only the BARE NOUN is blocked (whole-word) — the verb / adjective
        # form and plain descriptions pass.  This is the fix for a question
        # that is ABOUT the motif ("when should I sacrifice a piece?"): the
        # concept can't be dropped, so steer to a form that survives.
        forms = {
            "sacrifice": "\"sacrificing a piece\", \"a sacrificial attack\", or "
            "\"giving up material for the initiative\"",
            "fork": "\"a forking idea\", \"attacking two pieces at once\", or "
            "\"hitting two things at the same time\"",
            "pin": "\"pinning the piece\", \"a pinning idea\", or \"tying the "
            "piece down\"",
        }.get(word, f"the verb form or a plain description instead of the bare \"{word}\"")
        return (
            f"\n\nIMPORTANT: do not use the bare word \"{word}\" — the current "
            f"position shows no such concrete tactic, so the bare noun reads as "
            f"a claim about the board.  You may absolutely explain the IDEA: use "
            f"{forms} instead of the standalone word \"{word}\"." + _RETRY_KEEP_NATURAL
        )

    m = _SPECULATIVE_RE.search(msg)
    if m:
        word = m.group(1)
        extra = (
            "  You are the coach who has already digested the analysis — never "
            "mention it as \"the engine\"; just state the assessment."
            if word.lower() == "engine"
            else ""
        )
        return (
            f"\n\nIMPORTANT: do not use the word \"{word}\".  Phrase your "
            f"guidance as plain, confident coaching rather than speculation."
            + extra + _RETRY_KEEP_NATURAL
        )

    if "Mate not described" in msg:
        return (
            "\n\nIMPORTANT: the engine signal shows a forced mate — state that "
            "the outcome is \"inevitable\" (use that word), and never write "
            "\"checkmate\", \"forced mate\", or \"mate in N\"." + _RETRY_KEEP_NATURAL
        )

    m = _FORBIDDEN_PATTERN_RE.search(msg)
    if m:
        pat = m.group(1) or m.group(2) or ""
        if "[a-h]" in pat or "0-0" in pat or "O-O" in pat:
            return (
                "\n\nIMPORTANT: do not write any square or move notation "
                "(e4, Nf3, O-O).  Name pieces by role — \"your kingside knight\", "
                "\"the central pawn\", \"your king's bishop\"." + _RETRY_KEEP_NATURAL
            )
        if "white can" in pat or "black can" in pat:
            return (
                "\n\nIMPORTANT: do not phrase advice as \"White can\" / \"Black "
                "can\" or a move list; write it as flowing coaching prose."
                + _RETRY_KEEP_NATURAL
            )
        return (
            f"\n\nIMPORTANT: remove the phrase matching `{pat}` (an engine / "
            "notation / advisory term); explain the idea as a coach who has "
            "already digested the analysis." + _RETRY_KEEP_NATURAL
        )

    return _CHAT_RETRY_HINT

# ---------------------------------------------------------------------------
# Label tables (deterministic fallback)
# ---------------------------------------------------------------------------

_BAND_LABEL: dict[str, str] = {
    "equal": "equal",
    "small_advantage": "a small advantage",
    "clear_advantage": "a clear advantage",
    "decisive_advantage": "a decisive advantage",
}

# Warm, flowing phase plans (de-robotified 2026-07-08 — the old
# "In the opening, prioritise development and centre control." read like a
# textbook heading).  Each keeps its "In the {phase}" opener so the
# phase-tip pins (``test_chat_pipeline._PHASE_TIPS``) still match, but
# reads like a coach talking.
_PHASE_HINT: dict[str, str] = {
    "opening": "In the opening, the main things are to get your pieces out "
    "toward the centre and tuck your king away safely.",
    "middlegame": "In the middlegame, look to get your pieces active and stay "
    "alert for any tactics that come up.",
    "endgame": "In the endgame, bring your king into the game and push your "
    "passed pawns.",
}

# Delta phrased the way a coach would say it, not a clipped readout.  Kept
# as full sentences because ``_format_engine_context`` joins them after the
# eval sentence's period.  "stable" adds nothing worth saying aloud (the
# eval sentence already frames a steady position), so it is empty.
_DELTA_HINT: dict[str, str] = {
    "increase": "The momentum is heading that way right now.",
    "decrease": "It's a moment to steady the ship.",
    "stable": "",
}

# Move-quality as a natural sentence, not the "Last move quality: good."
# readout (de-robotified 2026-07-08).  Validator-safe (no notation / engine
# / mate vocabulary).
_MOVE_QUALITY_PHRASE: dict[str, str] = {
    "best": "That last move was the best choice available.",
    "excellent": "That last move was excellent.",
    "good": "That was a good, solid move.",
    "ok": "That last move was fine.",
    "inaccuracy": "That last move was a slight inaccuracy.",
    "mistake": "That last move was a mistake worth learning from.",
    "blunder": "That last move was a blunder — one to watch out for next time.",
}

# ---------------------------------------------------------------------------
# Question-type detection and level-differentiated coaching advice
# ---------------------------------------------------------------------------

_QUESTION_KEYWORDS: dict[str, list[str]] = {
    "endgame": [
        "endgame", "end game", "convert", "king activity", "rook end",
        "pawn end", "winning endgame",
    ],
    "opening": [
        "opening", "develop", "castle", "center", "centre", "piece out", "start",
    ],
    "strategic": [
        "plan", "strategy", "strategic", "structure", "long-term", "weak square",
        "outpost", "pawn chain", "imbalance",
    ],
    "tactical": [
        "tactic", "attack", "fork", "pin", "hanging", "capture",
        "threat", "combination", "sacrifice", "material", "win material",
    ],
}

_COACHING_ADVICE: dict[str, dict[str, str]] = {
    "tactical": {
        "beginner": (
            "Check if any pieces on the board are unprotected — "
            "these are often the first targets in tactics."
        ),
        "intermediate": (
            "Look for forcing moves: checks, captures, and threats. "
            "Undefended pieces are potential tactical targets."
        ),
        "advanced": (
            # "Calculate" is on the Mode-2 forbidden list — work through stays safe.
            "Work through all forcing replies. Assess candidate moves "
            "systematically: checks, captures, then threats."
        ),
    },
    "opening": {
        "beginner": (
            "Try to move each piece only once, control the centre with pawns, "
            "and get your king to safety."
        ),
        "intermediate": (
            "Develop purposefully: control the centre, avoid early queen moves, "
            "and coordinate your pieces before castling."
        ),
        "advanced": (
            "The pawn structure defines the resulting middlegame. "
            "Assess structural imbalances and proceed accordingly."
        ),
    },
    "endgame": {
        "beginner": (
            "Activate your king — it becomes a powerful piece in the endgame. "
            "Push your passed pawns."
        ),
        "intermediate": (
            "Use your king actively, centralise your rook, and look for pawn breaks "
            "to create a passed pawn."
        ),
        "advanced": (
            "Precise technique is essential. Determine key factors: "
            "king activity, pawn structure, and piece coordination."
        ),
    },
    "strategic": {
        "beginner": (
            "Find your least-active piece and look for a stronger square for it."
        ),
        "intermediate": (
            "Identify pawn weaknesses on both sides. Place your pieces on strong squares "
            "where they cannot easily be chased away."
        ),
        "advanced": (
            "Assess all imbalances: pawn structure, piece activity, weak squares, "
            "and pawn majorities. Form a concrete approach."
        ),
    },
    "general": {
        "beginner": (
            "Focus on piece safety first, then look for ways to improve your position."
        ),
        "intermediate": (
            # "Consider", "plan", and "engine" are all on the Mode-2 forbidden
            # lists (negative / structure / semantic).  Also drops the noun
            # "evaluation" because users read it as the coach quoting an
            # engine readout rather than offering coaching language — same
            # rationale as the _DELTA_HINT["stable"] rephrase above.
            "Look at the position carefully and think about your next two or three moves together."
        ),
        "advanced": (
            "Evaluate the position's key features: material, pawn structure, "
            "piece activity, and king safety."
        ),
    },
}


def _detect_question_type(query: str) -> str:
    q = query.lower()
    for qtype, keywords in _QUESTION_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return qtype
    return "general"


def _map_skill_level(player_profile: dict | None) -> str:
    if not player_profile:
        return "intermediate"
    skill = str(player_profile.get("skill_estimate", "")).lower()
    if "beginner" in skill or "novice" in skill:
        return "beginner"
    if "advanced" in skill or "expert" in skill or "master" in skill:
        return "advanced"
    return "intermediate"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatTurn:
    """A single turn in the conversation (user or assistant)."""

    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class ChatReply:
    """Result of generate_chat_reply().

    Attributes
    ----------
    reply : str
        Coaching reply referencing the engine evaluation.
    engine_signal : dict
        Structured engine signal from extract_engine_signal(); never
        derived from LLM or user input.
    mode : str
        Always "CHAT_V1" for this pipeline.
    """

    reply: str
    engine_signal: dict
    mode: str


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

_MAX_HISTORY_TURNS = 10  # last 5 exchanges kept in context


def _sanitize_field(value: str, max_len: int = 200) -> str:
    """Strip newlines and control chars from structured data before prompt embedding.

    Newlines are the primary vector for prompt-structure injection (an attacker
    embedding a fake [SYSTEM] or 'User:' header inside a player_profile field).
    Replaces every control character (< 0x20) and DEL (0x7F) with a space so
    the value stays readable but cannot introduce new prompt sections.
    """
    return "".join(c if c >= "\x20" else " " for c in str(value).replace("\x7f", " "))[:max_len].strip()


def _safe_sanitize(value: str, max_len: int = 60) -> str | None:
    """Combine the structural strip (``_sanitize_field``) with the
    injection-regex detector (``_sanitize`` / ``sanitize_user_query``).

    Used for **nested** player-profile leaves — mistake tags, strengths,
    skill estimate, past-mistake categories.  The Pydantic boundary
    validator on ``ChatRequest.player_profile`` already runs
    ``sanitize_user_query`` on STRING values, but a nested dict (e.g.
    ``common_mistakes: [{"tag": "ignore previous instructions..."}]``)
    walks past it: the leaf strings reach the prompt only through this
    pipeline, where pre-Sprint-5.B ``_sanitize_field`` stripped control
    chars but never ran the injection regex.  Audit finding F-09.

    Returns ``None`` when the value contains an injection pattern; the
    caller drops the field rather than substituting a placeholder, so
    the prompt stays minimal and the LLM never sees adversarial text.
    Clean values pass through unchanged (``sanitize_user_query`` is
    idempotent — see ``llm/rag/prompts/input_sanitizer.py``).
    """
    cleaned = _sanitize_field(value, max_len=max_len)
    if not cleaned:
        return None
    try:
        return _sanitize(cleaned)
    except ValueError:
        logger.warning(
            "Dropping player-profile field with injection-pattern content (orig len=%d)",
            len(str(value)),
        )
        return None


# Coach-voice tone instructions for the LLM system prompt.  Mapped
# from the Android SettingsBottomSheet radio (formal / conversational
# / terse).  None / unknown → no voice block (model uses default
# Mode-2 tone).  Mirrors the SettingsBottomSheet sub-label copy
# 1-for-1 so what users see in the toggle matches what the LLM is
# instructed to do.
_COACH_VOICE_INSTRUCTIONS = {
    "formal":         "Use a formal, precise, restrained tone.  Avoid contractions or casual phrasing.",
    "conversational": "Use a patient, scholarly conversational tone.  Welcoming but unhurried.",
    # The terse instruction must EXPLICITLY override the base prompt's
    # length guidance ("2–4 short paragraphs", closing "Focus on …"
    # line) — stated as tone-only, DeepSeek followed the base structure
    # and wrote 6–9 sentences (weekly stress runs, 2026-07-06; the
    # Category G scorecard flagged it on every execution).  The word
    # budget exists because a sentence cap alone gets gamed: after the
    # sentence fix the model complied with two ~50-word run-on
    # sentences (51 words/sentence in the 2026-07-06 scorecard
    # telemetry) — terse in structure, not in length.
    "terse": (
        "Be brief.  No flourish, no preamble — answer directly in one or two "
        "short sentences, at most about 30 words in total.  This length rule "
        "OVERRIDES the default length guidance: do NOT write multiple "
        "paragraphs, do NOT add a closing 'Focus on …' line, and do NOT chain "
        "long clauses together to stay within two sentences."
    ),
}

#: End-of-system reminder for the terse voice.  The voice block sits
#: directly after the base system prompt, and the base prompt's own
#: structure guidance follows much later context; repeating the hard
#: length cap as the LAST line of the system composition is what makes
#: the model actually obey it (same recency lesson as the Category C/D
#: prompt fixes).  Tone-only voices need no reminder.
_TERSE_REMINDER = (
    "\n\nREMINDER — TERSE VOICE: your entire reply must be at most two "
    "short sentences and about 30 words in total."
)


def _chat_engine_signal(fen: str, stockfish_json: dict | None = None) -> dict:
    """Engine signal for a chat turn.

    When the caller supplies a real Stockfish analysis (``stockfish_json`` from
    ``StockfishEnginePool.evaluate_position``) it carries the TRUE eval band +
    mate + flags.  Otherwise the signal is material-based eval PLUS the
    deterministic board-feature flags (hanging pieces, checks, king safety,
    material band, pawn structure), so the coach still grounds tactics in real
    facts.  Unparseable FEN degrades to the hollow material-only signal.
    """
    if stockfish_json is None:
        stockfish_json = {}
        try:
            board = chess.Board() if fen.strip() == "startpos" else chess.Board(fen)
            stockfish_json = {
                "tactical_flags": compute_tactical_flags(board),
                "position_flags": compute_position_flags(board),
            }
        except Exception:  # noqa: BLE001
            stockfish_json = {}
    return extract_engine_signal(stockfish_json, fen=fen)


def _build_chat_prompt(
    fen: str,
    messages: list[ChatTurn],
    player_profile: dict | None,
    engine_signal: dict,
    past_mistakes: list[str] | None = None,
    retry_hint: str = "",
    coach_voice: str | None = None,
    last_move: str | None = None,
    player_color: str = "white",
) -> str:
    """Assemble the fully-rendered Mode-2 prompt (system + voice + style +
    history + player context + RAG + FEN + sanitized user query).

    Extracted from ``_build_chat_llm`` so the streaming pipeline
    (``chat_stream_pipeline.stream_chat_reply``) builds a BYTE-IDENTICAL
    prompt — same trust-boundary sanitisation, same injection order — as
    the non-streaming path.  Keeping one builder is what prevents the two
    chat paths from drifting (cf. the validator-parity invariant).
    """
    # Sanitize latest user query
    user_turns = [t for t in messages if t.role == "user"]
    raw_query = user_turns[-1].content if user_turns else ""
    clean_query = _sanitize(raw_query)
    if retry_hint:
        clean_query = clean_query + retry_hint

    # Cereveon app-help: ALWAYS injected (see app_help.py) — a keyword gate
    # can't guarantee an app question is recognised, and a missed one falls
    # through to the constitution's "I can only help with chess" refusal,
    # which the product forbids.  The block is inert on a chess turn (the
    # model ignores it) and decisive on an app turn.  Placed right after the
    # static system prompt below so it sits in the cacheable prefix and the
    # position-specific content the mate gate depends on keeps recency.
    app_help_block = build_app_help_block()

    # Format conversation history (exclude latest user message)
    history_turns = messages[:-1] if messages else []
    history_lines: list[str] = []
    for turn in history_turns[-_MAX_HISTORY_TURNS:]:
        if turn.role == "system":
            history_lines.append(_sanitize_field(turn.content, max_len=500))
        else:
            role_label = "User" if turn.role == "user" else "Coach"
            history_lines.append(f"{role_label}: {_sanitize_field(turn.content, max_len=500)}")
    history_block = ""
    if history_lines:
        history_block = "\n\nCONVERSATION HISTORY:\n" + "\n".join(history_lines)

    # Player context block.  Every leaf that goes into the LLM prompt
    # runs through ``_safe_sanitize`` (control-char strip + injection-
    # regex) so adversarial text smuggled inside a nested
    # player_profile dict (audit F-09) gets dropped before assembly.
    player_block = ""
    if player_profile:
        skill = _safe_sanitize(player_profile.get("skill_estimate", ""), max_len=80)
        mistakes = player_profile.get("common_mistakes", [])
        strengths = player_profile.get("strengths", [])
        if skill:
            player_block += f"\nPlayer skill level: {skill}."
        if mistakes:
            tags = [
                _safe_sanitize(
                    m.get("tag", str(m)) if isinstance(m, dict) else str(m),
                    max_len=60,
                )
                for m in mistakes[:5]
            ]
            tags = [t for t in tags if t]
            if tags:
                player_block += f"\nRecurring mistake areas: {', '.join(tags)}."
        if strengths:
            safe_strengths = [_safe_sanitize(str(s), max_len=60) for s in strengths[:3]]
            safe_strengths = [s for s in safe_strengths if s]
            if safe_strengths:
                player_block += f"\nPlayer strengths: {', '.join(safe_strengths)}."
        if player_block:
            player_block = "\n\nPLAYER CONTEXT:" + player_block
    if past_mistakes:
        safe_mistakes = [_safe_sanitize(m, max_len=60) for m in past_mistakes[:5]]
        safe_mistakes = [m for m in safe_mistakes if m]
        if safe_mistakes:
            player_block += f"\nRecent training focus: {', '.join(safe_mistakes)}."

    # RAG retrieval + style block
    rag_docs = _retrieve(engine_signal, _DOCS)
    style_block = _build_clc(engine_signal)

    # Voice instruction goes immediately after the Mode-2 system
    # prompt, before per-conversation context, so the tone framing
    # is anchored by the strongest position in the prompt.  The
    # voice never overrides Mode-2 content rules — the system
    # prompt's "stick to engine truth" instruction is what's
    # authoritative; the voice only shapes phrasing.
    voice_block = ""
    if coach_voice and coach_voice in _COACH_VOICE_INSTRUCTIONS:
        voice_block = "\n\nCOACH VOICE: " + _COACH_VOICE_INSTRUCTIONS[coach_voice]

    # Live in-app games always have the human as White, but imported /
    # replayed games may have them as Black (the review board orients to the
    # player's side — PR #344), so the anchor colour comes from the caller
    # (default White for older clients).  Without a colour anchor the coach
    # reads the FEN side-to-move (the opponent, right after the player moves)
    # as the player and says "you are Black".  When the client sends the last
    # move, describe it in PLAIN ENGLISH (never a square: the no-notation
    # output rule strips "f3" and forces the canned fallback — the #247/#248
    # regression).
    anchor = "BLACK" if player_color == "black" else "WHITE"
    case_note = "Black (lowercase)" if player_color == "black" else "White (uppercase)"
    perspective_lines = [
        f"The player you are coaching is playing {anchor} — their pieces are the "
        f"{case_note} pieces.  The side-to-move shown below is only whose "
        f"turn it is, not the player's colour; read the engine evaluation from "
        f"{anchor.capitalize()}'s point of view."
    ]
    move_phrase = describe_move_plain(fen, last_move) if last_move else ""
    if move_phrase:
        perspective_lines.append(
            f"The player's most recent move (authoritative — describe it exactly "
            f"this way; do NOT re-derive the move from the board): they {move_phrase}. "
            'Refer to it in plain words ("your kingside pawn", "the kingside"), never in '
            "coordinate or move notation and never with a file letter."
        )
    perspective_block = "\n\nPLAYER PERSPECTIVE:\n" + "\n".join(perspective_lines)

    # Deterministic engine facts (hanging pieces, checks, king safety, material,
    # pawn structure) + what the last move attacks, so the coach grounds tactics
    # in real facts instead of inventing them. All plain-English / coordinate-free.
    # ``player_color`` flips the white-relative flag table so "you" stays the
    # player's side when they are Black (the Mode-1-proven flip machinery).
    fact_lines = render_engine_facts(engine_signal, player_color=player_color)
    threat = describe_threats(fen, last_move) if last_move else ""
    if threat:
        fact_lines.append(threat[0].upper() + threat[1:])
    facts_block = ""
    if fact_lines:
        facts_block = (
            "\n\nENGINE FACTS (authoritative — base every tactical or evaluative "
            "claim ONLY on these and the move above; do NOT invent threats, pins, "
            "forks, or mates that aren't listed):\n- " + "\n- ".join(fact_lines)
        )

    system = (
        _SYSTEM_PROMPT
        + app_help_block
        + voice_block
        + perspective_block
        + facts_block
        + "\n\n"
        + style_block
        + history_block
        + player_block
        + (_TERSE_REMINDER if coach_voice == "terse" else "")
        + APP_HELP_REMINDER  # truly last — the first-sentence directive needs max recency
    )

    prompt = _render(
        system_prompt=system,
        engine_signal=engine_signal,
        rag_docs=rag_docs,
        fen=fen,
        user_query=clean_query,
    )

    return prompt


def _build_chat_llm(
    fen: str,
    messages: list[ChatTurn],
    player_profile: dict | None,
    engine_signal: dict,
    past_mistakes: list[str] | None = None,
    retry_hint: str = "",
    coach_voice: str | None = None,
    last_move: str | None = None,
    player_color: str = "white",
) -> str:
    """Call the LLM with the Mode-2 prompt (non-streaming) and validate.

    Raises on any failure so the caller can fall back to _build_reply_deterministic.
    """
    prompt = _build_chat_prompt(
        fen,
        messages,
        player_profile,
        engine_signal,
        past_mistakes=past_mistakes,
        retry_hint=retry_hint,
        coach_voice=coach_voice,
        last_move=last_move,
        player_color=player_color,
    )

    response = _call_llm(prompt).strip()
    if not response:
        raise ValueError("Empty LLM response")

    # Every Mode-2 gate the API boundary will re-run at
    # ``validate_chat_response`` must run here too, so a borderline LLM
    # reply is caught inside the retry loop and either retried or falls
    # through to the deterministic ``_build_reply_deterministic`` — never
    # escapes this function to 500 ``/chat`` (or ``/chat/stream``).  The
    # client-side symptom of the missing gate was "Coach is offline"
    # appearing after a successful LLM call whose output happened to
    # fail the boundary's structure / semantic check.  Parity with
    # ``live_move_pipeline._build_hint_llm`` is enforced by the shared
    # helper and pinned by ``test_validator_parity.py``.
    validate_mode_2_or_raise(response, engine_signal)

    return response


# ---------------------------------------------------------------------------
# Deterministic fallback
# ---------------------------------------------------------------------------


def _format_engine_context(engine_signal: dict, player_color: str = "unknown") -> str:
    eval_info = engine_signal.get("evaluation", {})
    band = eval_info.get("band", "equal")
    side = eval_info.get("side", "unknown")
    eval_type = eval_info.get("type", "cp")
    phase = engine_signal.get("phase", "middlegame")
    delta = engine_signal.get("eval_delta", "stable")

    if eval_type == "mate":
        # See identical comment in live_move_pipeline.py — "Mate is
        # inevitable" satisfies the semantic mate-decisiveness require
        # (the gate accepts "inevitable" OR "forced") without triggering
        # the lexical MATE_CLAIM_PATTERNS forbid on `\bforce(?:d)? mate\b`.
        # Kept identical to live_move_pipeline so both deterministic
        # paths share the same engine-truth phrasing — see PR #167.
        #
        # When the player's colour is known (chat anchors the human as
        # White — see _build_chat_prompt; _build_reply_deterministic passes
        # it through), frame the winner in the second person to match the
        # Mode-1 deterministic hint.  Only the subject noun changes, so the
        # proven carrier still passes both gates.  Case-insensitive; unknown
        # colour OR side keeps the third person (preserves the gate pins and
        # the prompt-context test surface).
        side_l = side.lower() if isinstance(side, str) else ""
        color_l = player_color.lower() if isinstance(player_color, str) else ""
        if color_l in ("white", "black") and side_l in ("white", "black"):
            if side_l == color_l:
                eval_sentence = "Mate is inevitable — you secure the decisive outcome."
            else:
                eval_sentence = (
                    "Mate is inevitable — your opponent secures the decisive outcome."
                )
        elif side_l in ("white", "black"):
            eval_sentence = f"Mate is inevitable — {side} secures the decisive outcome."
        else:
            eval_sentence = "Mate is inevitable — the decisive outcome is sealed."
    elif band == "equal":
        # Equal-band: warm, flowing phrasing (de-robotified 2026-07-08 —
        # the old clipped "The position is roughly equal in the opening."
        # was the reply the user flagged as robotic).  Keeps the literal
        # "roughly equal" exactly once (the FORBIDDEN_EQUAL gate looks for
        # "slight advantage"/"winning", not "equal"; the phrase-count pin
        # in test_chat_pipeline forbids a DUPLICATE).
        eval_sentence = (
            f"You're in the {phase}, and the position is roughly equal — "
            f"balanced, with chances for both sides."
        )
    else:
        band_label = _BAND_LABEL.get(band, band.replace("_", " "))
        # Natural-language assessment (warmed 2026-07-08).  Frame from the
        # player's seat ("you" / "your opponent") to match the mate branch
        # and drop the detached lowercase "{side} has ..." readout.  Keeps
        # the band vocabulary ("advantage") the phrasing pins assert.
        # Unknown colour OR side falls back to a capitalised side name.
        side_l = side.lower() if isinstance(side, str) else ""
        color_l = player_color.lower() if isinstance(player_color, str) else ""
        if color_l in ("white", "black") and side_l in ("white", "black"):
            subject = "you have" if side_l == color_l else "your opponent has"
            eval_sentence = f"Here in the {phase}, {subject} {band_label}."
        else:
            side_label = side.capitalize() if isinstance(side, str) and side else side
            eval_sentence = f"Here in the {phase}, {side_label} has {band_label}."

    delta_hint = _DELTA_HINT.get(delta, "")
    return f"{eval_sentence} {delta_hint}".strip()


def _build_context_block(
    engine_signal: dict,
    player_profile: dict | None,
    past_mistakes: list[str] | None,
    move_count: int | None = None,
) -> str:
    """LLM-prompt-shaped context block.

    NOT used by the deterministic user-facing reply — see PR #169 —
    but kept as the unit-testable surface for the F-09 sanitisation
    defence (every nested player-profile / past-mistakes leaf passes
    through ``_safe_sanitize`` before assembly).  Production LLM
    callers consume the same ``_safe_sanitize`` defence inline in
    ``_build_chat_llm``; this helper stays here so the existing
    test_prompt_injection.py adversarial-input tests can exercise the
    sanitiser without spinning up the full prompt-construction path.

    If you find yourself reaching for the output of this helper in
    user-visible code: stop.  The metadata sentences
    ("This is move N of the game.", "Player skill level: …",
    "Recurring mistake areas: …", "Strengths: …", "Recent training
    focus: …") are LLM-prompt context, not coaching copy.
    """
    parts = [_format_engine_context(engine_signal)]

    if move_count is not None:
        parts.append(f"This is move {move_count} of the game.")

    # F-09: every nested player-profile leaf runs through ``_safe_sanitize``
    # (control-char strip + injection-regex) so adversarial text in mistake
    # tags / strengths / skill / past_mistakes cannot reach the LLM prompt.
    if player_profile:
        skill = _safe_sanitize(player_profile.get("skill_estimate", ""), max_len=80)
        mistakes = player_profile.get("common_mistakes", [])
        strengths = player_profile.get("strengths", [])
        if skill:
            parts.append(f"Player skill level: {skill}.")
        if mistakes:
            tags = [
                _safe_sanitize(
                    m.get("tag", str(m)) if isinstance(m, dict) else str(m),
                    max_len=60,
                )
                for m in mistakes[:5]
            ]
            tags = [t for t in tags if t]
            if tags:
                parts.append(f"Recurring mistake areas: {', '.join(tags)}.")
        if strengths:
            safe_strengths = [_safe_sanitize(str(s), max_len=60) for s in strengths[:3]]
            safe_strengths = [s for s in safe_strengths if s]
            if safe_strengths:
                parts.append(f"Strengths: {', '.join(safe_strengths)}.")

    if past_mistakes:
        safe_mistakes = [_safe_sanitize(m, max_len=60) for m in past_mistakes[:5]]
        safe_mistakes = [m for m in safe_mistakes if m]
        if safe_mistakes:
            parts.append(f"Recent training focus: {', '.join(safe_mistakes)}.")

    return " ".join(parts)


def _build_reply_deterministic(
    user_query: str,
    engine_signal: dict,
    history: list[ChatTurn],
    skill_level: str = "intermediate",
    coach_voice: str | None = None,
    player_color: str = "white",
) -> str:
    """Deterministic Mode-2 reply used when LLM is unavailable.

    The voice setting also shapes this path so the user gets a
    coherent experience when Ollama is unreachable — they shouldn't
    notice the LLM dropped out and suddenly start hearing a different
    tone.  Engine-derived facts (eval sentence, last move quality)
    are present in every voice; only the chatty/connective copy varies.

    Earlier revisions of this function appended a LLM-prompt-shaped
    "context block" that contained internal metadata — "This is move N
    of the game.", "Player skill level: intermediate.", "Recurring
    mistake areas: …", etc. — directly into the user-visible reply.
    On-device test 2026-05-16 exposed this as a UX bug: the user saw
    raw debug context surfacing in coach replies (see PR #169).  The
    metadata is still built for the LLM path's prompt by
    ``_build_chat_llm`` (which sanitises and formats the same fields
    with full F-09 defence) — it has no business in the deterministic
    user-facing reply.  Likewise, ``SafeExplainer.explain`` was
    appended on top of the eval sentence and produced the duplicated
    "Position is roughly equal." that the user reported.  Both are
    dropped: the eval sentence alone carries the engine-state framing
    and the question-type advice carries the actionable coaching.
    """
    voice = coach_voice if coach_voice in ("formal", "terse") else "conversational"
    parts: list[str] = []

    # Prior-question preface — terse skips it (preamble is the first
    # thing terse drops); formal swaps to a more restrained connector.
    # The raw prior-turn content is NOT echoed: this reply must pass the
    # Mode-2 boundary validator on every output, and any forbidden token
    # in a prior user message ("should", "consider", "Nf3", etc.) would
    # otherwise leak straight into the response and trip a 500.
    prior_user_turns = [t for t in history[:-1] if t.role == "user"]
    if prior_user_turns and voice != "terse":
        if voice == "formal":
            parts.append("Regarding your earlier inquiry:")
        else:
            parts.append("Following up on your earlier question:")

    # Engine-state framing — single sentence, no internal metadata.  The
    # anchor colour comes from the caller (White for live in-app games,
    # possibly Black for imported/replayed games — see _build_chat_prompt),
    # so the mate winner is framed from the PLAYER's seat ("you" / "your
    # opponent") rather than the detached third-person side name.
    parts.append(_format_engine_context(engine_signal, player_color=player_color))

    move_quality = engine_signal.get("last_move_quality", "")
    if move_quality and move_quality not in ("unknown", ""):
        # Natural sentence instead of the "Last move quality: good." readout.
        parts.append(_MOVE_QUALITY_PHRASE.get(move_quality, ""))

    # Once mate is inevitable the game is decided, so the generic phase
    # tip ("push your passed pawns") and the open-ended question advice
    # ("think about your next two or three moves") read as incongruous
    # filler tacked onto a finished game (surfaced 2026-07-09 testing the
    # warmed fallback on real positions).  The mate sentence + move-quality
    # line already answer the user; suppress both generic blocks on mate.
    is_mate = engine_signal.get("evaluation", {}).get("type") == "mate"

    # Phase tip — Mode-2 includes it by default, but it's exactly the
    # kind of generic filler the user opted out of in terse mode.
    if voice != "terse" and not is_mate:
        phase = engine_signal.get("phase", "middlegame")
        phase_tip = _PHASE_HINT.get(phase, "")
        if phase_tip:
            parts.append(phase_tip)

    query = user_query.strip()
    if query and not is_mate:
        # _detect_question_type reads the raw query (lowercased keyword
        # match) but the result is never substituted back into the reply —
        # see prior-turn note above for the same Mode-2-leak rationale.
        question_type = _detect_question_type(query)
        advice = _COACHING_ADVICE[question_type][skill_level]
        if voice == "terse":
            parts.append(advice)
        elif voice == "formal":
            parts.append(f"On the matter at hand: {advice}")
        else:
            parts.append(f"In response to your question: {advice}")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_chat_reply(
    fen: str,
    messages: list[ChatTurn],
    player_profile: dict | None = None,
    past_mistakes: list[str] | None = None,
    move_count: int | None = None,
    coach_voice: str | None = None,
    last_move: str | None = None,
    stockfish_json: dict | None = None,
    force_deterministic: bool = False,
    player_color: str = "white",
) -> ChatReply:
    """Generate a coaching reply for the current chat turn.

    Attempts the LLM path first (full Mode-2 pipeline with RAG, history,
    and validation); falls back to the deterministic reply when Ollama is
    unavailable or validation fails.

    Parameters
    ----------
    fen:
        Current board position (FEN string or "startpos").
    messages:
        Full conversation history including the latest user message at the end.
    player_profile:
        Optional SECA player model dict (skill_estimate, common_mistakes, strengths).
    past_mistakes:
        Optional list of MistakeCategory strings from the analytics layer.
    move_count:
        Optional half-move count; injected into deterministic context block.
    force_deterministic:
        Skip the LLM path entirely and emit the deterministic reply.
        Used by the ``/chat`` and ``/chat/stream`` handlers' safety nets
        when the boundary validator (``validate_chat_response``) rejects
        a pipeline reply.  The deterministic builder
        (``_build_reply_deterministic``) is constructed to satisfy every
        Mode-2 gate by construction so this re-call cannot 500 on the
        same validator drift.

    Returns
    -------
    ChatReply
        reply         — coaching explanation referencing engine evaluation.
        engine_signal — from extract_engine_signal(); never from LLM.
        mode          — always "CHAT_V1".
    """
    engine_signal = _chat_engine_signal(fen, stockfish_json)

    # Auto-compact long histories before any processing to reduce token usage.
    if should_compact(messages):
        messages = compact_history(messages)

    # --- LLM path with retry ---
    if _LLM_AVAILABLE and not force_deterministic:
        retry_hint = ""
        # Track the most-recent validator failure so the natural-
        # exhaustion path (no `break`, just AssertionError / Mode2Violation
        # firing _CHAT_MAX_RETRIES+1 times in a row) can emit a WARNING
        # that mirrors live_move_pipeline's exhaustion log.  Before this
        # was added, chat could silently fall through to the deterministic
        # reply with no operator signal — Mode-1 logged it, Mode-2 didn't,
        # which was the diagnostic gap exposed by the 2026-05-15 live
        # device test (user saw templated chat replies; no warning told
        # operators *why* the LLM path bailed).  Captured as `last_*`
        # rather than raised so the retry loop keeps its existing
        # control flow (retry on validator failure, break only on
        # firewall / transport / unknown).
        last_validator_exc: Exception | None = None
        for attempt in range(_CHAT_MAX_RETRIES + 1):
            if attempt > 0:
                time.sleep(_CHAT_RETRY_DELAY_SECONDS)
            try:
                reply = _build_chat_llm(
                    fen, messages, player_profile, engine_signal, past_mistakes,
                    retry_hint=retry_hint,
                    coach_voice=coach_voice,
                    last_move=last_move,
                    player_color=player_color,
                )
                # Spurious-refusal backstop: a canned position-refusal that
                # cannot be correct here (see _is_spurious_refusal) is a soft
                # failure — re-ask with a neutral hint rather than shipping
                # it.  Only on non-final attempts; the final attempt returns
                # whatever it got (still a valid Mode-2 reply).
                if attempt < _CHAT_MAX_RETRIES and _is_spurious_refusal(reply, engine_signal):
                    logger.debug("Chat backstop: spurious refusal; retrying with neutral hint")
                    retry_hint = _SPURIOUS_RETRY_HINT
                    continue
                # ESV structural integrity check (programming-error guard; never from LLM).
                _EngineSignalSchema.model_validate(engine_signal)
                return ChatReply(reply=reply, engine_signal=engine_signal, mode="CHAT_V1")
            except _OutputFirewallError:
                # Working-as-intended safety event (LLM said something
                # the firewall caught) — kept at DEBUG so noisy
                # adversarial users don't fill production logs.
                logger.debug("Chat LLM blocked by output firewall; using deterministic fallback")
                break
            except AssertionError as exc:
                # Mode-2 negative OR structure validator failed — retry
                # with a TARGETED hint naming the exact offending token so
                # the model rephrases and passes, instead of re-failing the
                # same way and exhausting to the robotic fallback.
                last_validator_exc = exc
                retry_hint = _targeted_retry_hint(exc)
            except _Mode2Violation as exc:
                # Mode-2 semantic violation (equal-band drift, mate
                # misframing, invented tactic).  Targeted rephrase hint —
                # this is the common "how do I play a gambit?" fallback
                # cause ("winning"/"sacrifice" as a concept on a level
                # board).
                last_validator_exc = exc
                retry_hint = _targeted_retry_hint(exc)
            except Exception as exc:  # noqa: BLE001
                # Production-impacting: Ollama unreachable, model not
                # pulled, transport timeout, etc.  All callers continue
                # with the deterministic fallback so users still get
                # *some* coach reply, but the operator must be able to
                # see this in `docker compose logs api` without
                # redeploying with debug logging — promoted to WARNING.
                logger.warning(
                    "Mode-2 LLM path failed (%s: %s); using deterministic fallback",
                    type(exc).__name__,
                    exc,
                )
                break
        else:
            # for...else: ran when the loop exited via natural exhaustion
            # (every `break` path skips this clause).  If we got here AND
            # we have a recorded validator exception, retries were
            # exhausted by repeated validator rejections — mirror
            # live_move_pipeline.py's exhaustion WARNING so operators
            # have a single greppable phrase ("Mode-{1,2} LLM failed
            # after N attempts") across both pipelines.
            if last_validator_exc is not None:
                logger.warning(
                    "Mode-2 LLM failed after %d attempts (%s: %s); using deterministic fallback",
                    _CHAT_MAX_RETRIES + 1,
                    type(last_validator_exc).__name__,
                    last_validator_exc,
                )

    # --- Deterministic fallback ---
    # Note: ``player_profile``, ``past_mistakes``, ``move_count`` are
    # intentionally NOT threaded into the deterministic reply — those
    # are LLM-prompt-only context (see ``_build_chat_llm``).  Earlier
    # revisions concatenated them as raw sentences into the user-visible
    # reply ("This is move 6 of the game.  Player skill level:
    # intermediate."), exposing internal metadata as if it were coaching.
    # See PR #169 / 2026-05-16 on-device test.  ``_safe_explainer.explain``
    # is also dropped here for the same reason: it duplicated the eval
    # sentence already produced by ``_format_engine_context`` inside
    # ``_build_reply_deterministic``.
    user_turns = [t for t in messages if t.role == "user"]
    user_query = user_turns[-1].content if user_turns else ""
    skill_level = _map_skill_level(player_profile)

    reply = _build_reply_deterministic(
        user_query=user_query,
        engine_signal=engine_signal,
        history=messages,
        skill_level=skill_level,
        coach_voice=coach_voice,
        player_color=player_color,
    )
    return ChatReply(reply=reply, engine_signal=engine_signal, mode="CHAT_V1")
