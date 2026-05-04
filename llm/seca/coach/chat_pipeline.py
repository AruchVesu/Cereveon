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
import time
from dataclasses import dataclass

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.seca.coach.context_compact import compact_history, should_compact
from llm.seca.explainer.safe_explainer import SafeExplainer

logger = logging.getLogger(__name__)

_safe_explainer = SafeExplainer()

# ---------------------------------------------------------------------------
# Optional LLM imports
# ---------------------------------------------------------------------------

try:
    from llm.explain_pipeline import call_llm as _call_llm  # type: ignore[import]
    from llm.rag.prompts.system_v2_mode_2 import SYSTEM_PROMPT as _SYSTEM_PROMPT  # type: ignore[import]
    from llm.rag.prompts.mode_2.render import render_mode_2_prompt as _render  # type: ignore[import]
    from llm.rag.retriever.retriever import retrieve as _retrieve  # type: ignore[import]
    from llm.rag.documents import ALL_RAG_DOCUMENTS as _DOCS  # type: ignore[import]
    from llm.confidence_language_controller import build_language_controller_block as _build_clc  # type: ignore[import]
    from llm.rag.validators.mode_2_negative import validate_mode_2_negative as _validate_neg  # type: ignore[import]
    from llm.rag.prompts.input_sanitizer import sanitize_user_query as _sanitize  # type: ignore[import]
    from llm.rag.safety.output_firewall import (  # type: ignore[import]
        check_output as _check_output,
        OutputFirewallError as _OutputFirewallError,
    )
    from llm.rag.validators.explain_response_schema import EngineSignalSchema as _EngineSignalSchema  # type: ignore[import]
    _LLM_AVAILABLE = True
except Exception as _llm_import_exc:  # noqa: BLE001
    logger.warning("LLM imports unavailable — deterministic path only: %s", _llm_import_exc)
    _LLM_AVAILABLE = False

_CHAT_MAX_RETRIES = 2
_CHAT_RETRY_DELAY_SECONDS = 0.5
_CHAT_RETRY_HINT = (
    "\n\nIMPORTANT: Follow MODE-2 rules strictly. "
    "Do NOT speculate, invent moves, or mention engine intentions."
)

# ---------------------------------------------------------------------------
# Label tables (deterministic fallback)
# ---------------------------------------------------------------------------

_BAND_LABEL: dict[str, str] = {
    "equal": "equal",
    "small_advantage": "a small advantage",
    "clear_advantage": "a clear advantage",
    "decisive_advantage": "a decisive advantage",
}

_PHASE_HINT: dict[str, str] = {
    "opening": "In the opening, prioritise development and centre control.",
    "middlegame": "In the middlegame, look for tactical motifs and improve piece activity.",
    "endgame": "In the endgame, activate the king and convert any material advantage.",
}

_DELTA_HINT: dict[str, str] = {
    "increase": "The position is improving for the side to move.",
    "decrease": "The position has deteriorated — caution is warranted.",
    "stable": "The evaluation is stable.",
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
            "Assess structural imbalances and plan accordingly."
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
            "Find your least-active piece and look for a better square for it."
        ),
        "intermediate": (
            "Identify pawn weaknesses on both sides. Place your pieces on strong squares "
            "where they cannot easily be chased away."
        ),
        "advanced": (
            "Assess all imbalances: pawn structure, piece activity, weak squares, "
            "and pawn majorities. Create a concrete plan."
        ),
    },
    "general": {
        "beginner": (
            "Focus on piece safety first, then look for ways to improve your position."
        ),
        "intermediate": (
            # "Consider" is on the Mode-2 forbidden list — "Use" stays safe.
            "Use the engine evaluation and think about your next two or three moves as a plan."
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


# Coach-voice tone instructions for the LLM system prompt.  Mapped
# from the Android SettingsBottomSheet radio (formal / conversational
# / terse).  None / unknown → no voice block (model uses default
# Mode-2 tone).  Mirrors the SettingsBottomSheet sub-label copy
# 1-for-1 so what users see in the toggle matches what the LLM is
# instructed to do.
_COACH_VOICE_INSTRUCTIONS = {
    "formal":         "Use a formal, precise, restrained tone.  Avoid contractions or casual phrasing.",
    "conversational": "Use a patient, scholarly conversational tone.  Welcoming but unhurried.",
    "terse":          "Be brief.  No flourish, no preamble — answer directly in one or two short sentences.",
}


def _build_chat_llm(
    fen: str,
    messages: list[ChatTurn],
    player_profile: dict | None,
    engine_signal: dict,
    past_mistakes: list[str] | None = None,
    retry_hint: str = "",
    coach_voice: str | None = None,
) -> str:
    """Call the LLM with Mode-2 prompt including conversation history.

    Raises on any failure so the caller can fall back to _build_reply_deterministic.
    """
    # Sanitize latest user query
    user_turns = [t for t in messages if t.role == "user"]
    raw_query = user_turns[-1].content if user_turns else ""
    clean_query = _sanitize(raw_query)
    if retry_hint:
        clean_query = clean_query + retry_hint

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

    # Player context block
    player_block = ""
    if player_profile:
        skill = _sanitize_field(player_profile.get("skill_estimate", ""), max_len=80)
        mistakes = player_profile.get("common_mistakes", [])
        strengths = player_profile.get("strengths", [])
        if skill:
            player_block += f"\nPlayer skill level: {skill}."
        if mistakes:
            tags = [_sanitize_field(m.get("tag", str(m)) if isinstance(m, dict) else str(m), max_len=60)
                    for m in mistakes[:5]]
            player_block += f"\nRecurring mistake areas: {', '.join(tags)}."
        if strengths:
            player_block += f"\nPlayer strengths: {', '.join(_sanitize_field(str(s), max_len=60) for s in strengths[:3])}."
        if player_block:
            player_block = "\n\nPLAYER CONTEXT:" + player_block
    if past_mistakes:
        safe_mistakes = [_sanitize_field(m, max_len=60) for m in past_mistakes[:5]]
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

    system = _SYSTEM_PROMPT + voice_block + "\n\n" + style_block + history_block + player_block

    prompt = _render(
        system_prompt=system,
        engine_signal=engine_signal,
        rag_docs=rag_docs,
        fen=fen,
        user_query=clean_query,
    )

    response = _call_llm(prompt).strip()
    if not response:
        raise ValueError("Empty LLM response")

    # Output firewall + Mode-2 negative validation
    _check_output(response)
    _validate_neg(response)

    return response


# ---------------------------------------------------------------------------
# Deterministic fallback
# ---------------------------------------------------------------------------


def _format_engine_context(engine_signal: dict) -> str:
    eval_info = engine_signal.get("evaluation", {})
    band = eval_info.get("band", "equal")
    side = eval_info.get("side", "unknown")
    eval_type = eval_info.get("type", "cp")
    phase = engine_signal.get("phase", "middlegame")
    delta = engine_signal.get("eval_delta", "stable")

    if eval_type == "mate":
        eval_sentence = f"The engine sees a forced mate ({side} is winning)."
    else:
        band_label = _BAND_LABEL.get(band, band.replace("_", " "))
        eval_sentence = f"Engine evaluation: {side} has {band_label} [{phase}]."

    delta_hint = _DELTA_HINT.get(delta, "")
    return f"{eval_sentence} {delta_hint}".strip()


def _build_context_block(
    engine_signal: dict,
    player_profile: dict | None,
    past_mistakes: list[str] | None,
    move_count: int | None = None,
) -> str:
    parts = [_format_engine_context(engine_signal)]

    if move_count is not None:
        parts.append(f"This is move {move_count} of the game.")

    if player_profile:
        skill = _sanitize_field(player_profile.get("skill_estimate", ""), max_len=80)
        mistakes = player_profile.get("common_mistakes", [])
        strengths = player_profile.get("strengths", [])
        if skill:
            parts.append(f"Player skill level: {skill}.")
        if mistakes:
            tags = [_sanitize_field(m.get("tag", str(m)) if isinstance(m, dict) else str(m), max_len=60)
                    for m in mistakes[:5]]
            parts.append(f"Recurring mistake areas: {', '.join(tags)}.")
        if strengths:
            parts.append(f"Strengths: {', '.join(_sanitize_field(str(s), max_len=60) for s in strengths[:3])}.")

    if past_mistakes:
        safe_mistakes = [_sanitize_field(m, max_len=60) for m in past_mistakes[:5]]
        parts.append(f"Recent training focus: {', '.join(safe_mistakes)}.")

    return " ".join(parts)


def _build_reply_deterministic(
    user_query: str,
    context_block: str,
    engine_signal: dict,
    base_explanation: str,
    history: list[ChatTurn],
    skill_level: str = "intermediate",
    coach_voice: str | None = None,
) -> str:
    """Deterministic Mode-2 reply used when LLM is unavailable.

    The voice setting also shapes this path so the user gets a
    coherent experience when Ollama is unreachable — they shouldn't
    notice the LLM dropped out and suddenly start hearing a different
    tone.  Engine-derived facts (context_block, base_explanation,
    last move quality) are present in every voice; only the
    chatty/connective copy varies.
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

    parts.append(context_block)

    move_quality = engine_signal.get("last_move_quality", "")
    if move_quality and move_quality not in ("unknown", ""):
        parts.append(f"Last move quality: {move_quality}.")

    if base_explanation:
        parts.append(base_explanation)

    # Phase tip — Mode-2 includes it by default, but it's exactly the
    # kind of generic filler the user opted out of in terse mode.
    if voice != "terse":
        phase = engine_signal.get("phase", "middlegame")
        phase_tip = _PHASE_HINT.get(phase, "")
        if phase_tip:
            parts.append(phase_tip)

    query = user_query.strip()
    if query:
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

    Returns
    -------
    ChatReply
        reply         — coaching explanation referencing engine evaluation.
        engine_signal — from extract_engine_signal(); never from LLM.
        mode          — always "CHAT_V1".
    """
    engine_signal = extract_engine_signal({}, fen=fen)

    # Auto-compact long histories before any processing to reduce token usage.
    if should_compact(messages):
        messages = compact_history(messages)

    # --- LLM path with retry ---
    if _LLM_AVAILABLE:
        retry_hint = ""
        for attempt in range(_CHAT_MAX_RETRIES + 1):
            if attempt > 0:
                time.sleep(_CHAT_RETRY_DELAY_SECONDS)
            try:
                reply = _build_chat_llm(
                    fen, messages, player_profile, engine_signal, past_mistakes,
                    retry_hint=retry_hint,
                    coach_voice=coach_voice,
                )
                # ESV structural integrity check (programming-error guard; never from LLM).
                _EngineSignalSchema.model_validate(engine_signal)
                return ChatReply(reply=reply, engine_signal=engine_signal, mode="CHAT_V1")
            except _OutputFirewallError:
                logger.debug("Chat LLM blocked by output firewall; using deterministic fallback")
                break
            except AssertionError:
                # Mode-2 negative validator failed — retry with stricter hint.
                retry_hint = _CHAT_RETRY_HINT
            except Exception as exc:  # noqa: BLE001
                logger.debug("Mode-2 LLM path failed (%s); using deterministic fallback", exc)
                break

    # --- Deterministic fallback ---
    base_explanation = _safe_explainer.explain(engine_signal)
    context_block = _build_context_block(
        engine_signal, player_profile, past_mistakes, move_count
    )
    user_turns = [t for t in messages if t.role == "user"]
    user_query = user_turns[-1].content if user_turns else ""
    skill_level = _map_skill_level(player_profile)

    reply = _build_reply_deterministic(
        user_query=user_query,
        context_block=context_block,
        engine_signal=engine_signal,
        base_explanation=base_explanation,
        history=messages,
        skill_level=skill_level,
        coach_voice=coach_voice,
    )
    return ChatReply(reply=reply, engine_signal=engine_signal, mode="CHAT_V1")
