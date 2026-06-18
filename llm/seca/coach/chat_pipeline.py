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
    # "The position is stable." replaces an earlier "The evaluation is
    # stable." — the literal word ``evaluation`` framed the coach as a
    # readout of engine data rather than a player-facing assessment, and
    # the user perceived it as the LLM leaking the engine signal even
    # when it came from the deterministic fallback.  The new phrasing
    # carries the same delta information.
    "stable": "The position is stable.",
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
    "terse":          "Be brief.  No flourish, no preamble — answer directly in one or two short sentences.",
}


def _build_chat_prompt(
    fen: str,
    messages: list[ChatTurn],
    player_profile: dict | None,
    engine_signal: dict,
    past_mistakes: list[str] | None = None,
    retry_hint: str = "",
    coach_voice: str | None = None,
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

    # The Android client only lets the human play White (local games), but the
    # Mode-2 prompt never said so.  Right after the player's move the FEN's
    # side-to-move is Black (the opponent), and with no perspective anchor the
    # coach concluded "you are playing Black" and misattributed pieces (the
    # in-app report: it called the player's f-pawn the "king's pawn").  State
    # the perspective explicitly.  The live Mode-1 prompt already does this via
    # player_color; chat never got it.  (When Black play is added, thread the
    # colour from the client instead of assuming White.)
    perspective_block = (
        "\n\nPLAYER PERSPECTIVE:\n"
        "The player you are coaching is playing WHITE — their pieces are the "
        "White (uppercase) pieces.  The position's side-to-move indicates only "
        "whose turn it is, NOT the player's colour: immediately after the player "
        "moves it is the opponent's (Black's) turn.  Always address the player as "
        "White, and read the engine evaluation from White's point of view."
    )

    system = (
        _SYSTEM_PROMPT
        + voice_block
        + perspective_block
        + "\n\n"
        + style_block
        + history_block
        + player_block
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


def _format_engine_context(engine_signal: dict) -> str:
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
        eval_sentence = f"Mate is inevitable — {side} secures the decisive outcome."
    elif band == "equal":
        # Equal-band: avoid the "{side} has equal" phrasing (awkward) and
        # the "Evaluation:" prefix (reads as engine readout).  The
        # FORBIDDEN_EQUAL list still applies — "roughly equal" is OK
        # (the gate looks for "slight advantage" / "better" / etc., not
        # "equal" itself).
        eval_sentence = f"The position is roughly equal in the {phase}."
    else:
        band_label = _BAND_LABEL.get(band, band.replace("_", " "))
        # Plain natural-language assessment.  Drops the pre-Sprint-5.A
        # "Engine" prefix (forbidden by FORBIDDEN_ENGINE_SPECULATION)
        # and the "Evaluation:" prefix that succeeded it (reads as an
        # engine readout rather than a coaching assessment — see
        # _DELTA_HINT["stable"] for the same rationale).  All three
        # Mode-2 gates pass on the resulting sentence by construction.
        eval_sentence = f"{side.capitalize()} has {band_label} in the {phase}."

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

    # Engine-state framing — single sentence, no internal metadata.
    parts.append(_format_engine_context(engine_signal))

    move_quality = engine_signal.get("last_move_quality", "")
    if move_quality and move_quality not in ("unknown", ""):
        parts.append(f"Last move quality: {move_quality}.")

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
    force_deterministic: bool = False,
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
    engine_signal = extract_engine_signal({}, fen=fen)

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
                )
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
                # with stricter hint; the retry path is the right one
                # because LLMs often recover when re-asked with explicit
                # rules.
                last_validator_exc = exc
                retry_hint = _CHAT_RETRY_HINT
            except _Mode2Violation as exc:
                # Mode-2 semantic violation (equal-band drift, mate
                # misframing, invented tactic).  Same retry behaviour as
                # AssertionError above so the LLM gets a second chance
                # with the stricter system prompt addition.
                last_validator_exc = exc
                retry_hint = _CHAT_RETRY_HINT
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
    )
    return ChatReply(reply=reply, engine_signal=engine_signal, mode="CHAT_V1")
