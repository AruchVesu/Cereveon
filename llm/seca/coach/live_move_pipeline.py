"""
Per-move live coaching pipeline — LLM-powered with deterministic fallback.

Architecture
------------
Inputs:
    fen               Board position (FEN) after the human's move.
    uci               The human's move in UCI notation (e.g. "e2e4").
    player_id         Player identifier.
    explanation_style Player skill style: "simple" | "intermediate" | "advanced".

Processing:
    1. Extract engine signal from FEN via extract_engine_signal()
       (no Stockfish process required — heuristic from FEN).
    2. Try LLM path:
       a. Build Mode-1 prompt (system prompt + engine context + RAG snippets).
       b. Call Ollama; validate response.
       c. Return LLM-generated 1-2 sentence coaching hint.
    3. On any LLM failure, fall back to the deterministic _build_hint().
    4. Return LiveMoveReply(hint, engine_signal, move_quality, mode="LIVE_V1").

Constraints
-----------
- No reinforcement learning.
- No dynamic skill adaptation.
- engine_signal is always produced by extract_engine_signal(), never
  sourced from user input.
- LLM hint is constrained to 1-2 sentences by the system prompt.
- Deterministic fallback always available when LLM is unreachable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from llm.seca.coach.confidence_language_controller import compute_urgency
from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.seca.explainer.safe_explainer import SafeExplainer

logger = logging.getLogger(__name__)

_safe_explainer = SafeExplainer()

# ---------------------------------------------------------------------------
# Optional LLM imports — absent when httpx / Ollama stack is not installed
# ---------------------------------------------------------------------------

try:
    from llm.seca.coach.explain_pipeline import call_llm as _call_llm  # type: ignore[import]
    from llm.rag.prompts.system_mode_1 import SYSTEM_PROMPT_MODE_1  # type: ignore[import]
    from llm.rag.prompts.mode_1.render import render_mode_1_prompt  # type: ignore[import]
    from llm.rag.retriever.retriever import retrieve as _retrieve  # type: ignore[import]
    from llm.rag.documents import ALL_RAG_DOCUMENTS as _DOCS  # type: ignore[import]
    # Mode-1 hints must satisfy the same in-pipeline safety contract as
    # Mode-2 chat replies: the output firewall blocks prompt-leak,
    # role-bypass, identity, PII-credential, and harmful patterns; the
    # Mode-2 negative validator blocks engine/move/calculation language.
    # Without these calls, a Mode-1 hint that says "I am ChatGPT" or
    # "Stockfish recommends Nf3" would only be caught at the HTTP
    # boundary (validate_live_move_response) — that boundary only re-
    # runs the negative regex and would still leak firewall-class
    # content.  Mirrors the chat_pipeline.py pattern.
    from llm.rag.safety.output_firewall import check_output as _check_output  # type: ignore[import]
    from llm.rag.validators.mode_2_negative import validate_mode_2_negative as _validate_neg  # type: ignore[import]
    # Mode-2 structure + semantic must also run inside the retry loop so
    # the pipeline can retry / fall back instead of letting a borderline
    # hint reach the API boundary's `validate_live_move_response` and
    # 500 the route.  Without these, an LLM hint that uses "consider" /
    # "plan" (structure) or "slight advantage" on an equal-band
    # position (semantic) passes `_validate_neg` here, gets returned to
    # `/live/move`, then the boundary validator raises
    # `ExplainSchemaError` → 500 → token rotation cascade locks the
    # session.  See issue #129.
    from llm.rag.validators.mode_2_structure import (  # type: ignore[import]
        validate_mode_2_structure as _validate_struct,
    )
    from llm.rag.validators.mode_2_semantic import (  # type: ignore[import]
        validate_mode_2_semantic as _validate_sem,
        Mode2Violation as _Mode2Violation,
    )
    _LLM_AVAILABLE = True
except Exception as _llm_import_exc:  # noqa: BLE001
    logger.warning("LLM imports unavailable — deterministic path only: %s", _llm_import_exc)
    _LLM_AVAILABLE = False

_LIVE_MAX_RETRIES = 2
_LIVE_RETRY_DELAY_SECONDS = 0.5

# ---------------------------------------------------------------------------
# Label tables (deterministic fallback)
# ---------------------------------------------------------------------------

_BAND_LABEL: dict[str, str] = {
    "equal": "equal",
    "small_advantage": "a small advantage",
    "clear_advantage": "a clear advantage",
    "decisive_advantage": "a decisive advantage",
}

# Brief phase tip suffix appended to the eval sentence in intermediate/advanced styles.
# Uses different phrasing from Mode-2 (_PHASE_HINT in chat_pipeline) to keep layers distinct.
_PHASE_TIP: dict[str, str] = {
    "opening": "focus on development and centre control",
    "middlegame": "look for active piece play",
    "endgame": "activate the king and push your pawns",
}

# Level-differentiated quality comments used by the deterministic fallback.
_QUALITY_COMMENT: dict[str, dict[str, str]] = {
    # NB: ``better`` is on validate_mode_2_semantic's FORBIDDEN_EQUAL list
    # (rejected when band == "equal").  ``engine`` is on its
    # FORBIDDEN_ENGINE_SPECULATION list (rejected unconditionally).
    # ``consider`` is on validate_mode_2_structure's FORBIDDEN_SECTIONS.
    # The pre-Sprint-5.A wording used all three; the rewrite below
    # substitutes ``stronger`` / ``review`` / no-engine phrasing so the
    # deterministic fallback survives all three gates regardless of band.
    "blunder": {
        "simple": "Oops — that was a blunder. A piece was left unprotected.",
        "intermediate": "That was a blunder — look for a stronger continuation next time.",
        "advanced": "That was a blunder — a significant error that concedes material or position.",
    },
    "mistake": {
        "simple": "That move gave away too much — try to protect your pieces.",
        "intermediate": "That move was a mistake — review the alternatives.",
        "advanced": "That move was a mistake — a stronger alternative was available.",
    },
    "inaccuracy": {
        "simple": "You had a stronger move there — keep looking for improvements.",
        "intermediate": "A slight inaccuracy — you had a stronger option.",
        "advanced": "An inaccuracy — a more precise continuation was available.",
    },
    "good": {
        "simple": "Nice move!",
        "intermediate": "Good move — that was a strong choice.",
        "advanced": "Good move — that maintains a solid position.",
    },
    "excellent": {
        "simple": "Great move!",
        "intermediate": "Excellent move — one of the best continuations.",
        "advanced": "Excellent move — among the strongest continuations.",
    },
    "best": {
        "simple": "Perfect move!",
        "intermediate": "Best move — this matches the strongest continuation.",
        "advanced": "Best move — the strongest continuation.",
    },
}

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveMoveReply:
    """Result of generate_live_reply().

    Attributes
    ----------
    hint : str
        1-2 sentence coaching hint referencing the engine evaluation.
    engine_signal : dict
        Structured engine signal from extract_engine_signal(); never
        derived from user input.
    move_quality : str
        last_move_quality from the engine signal, or "unknown".
    mode : str
        Always "LIVE_V1" for this pipeline.
    """

    hint: str
    engine_signal: dict
    move_quality: str
    mode: str


# ---------------------------------------------------------------------------
# Deterministic fallback hint builder
# ---------------------------------------------------------------------------


def _build_hint(
    uci: str,
    engine_signal: dict,
    base_explanation: str,
    explanation_style: str | None = None,
) -> str:
    """Deterministic 1-2 sentence coaching hint (LLM fallback).

    Leads with move-quality feedback (most relevant to the player), then
    appends a single evaluation context sentence.  Phase-specific tips are
    intentionally absent — they belong in Mode 2 (the chat / LLM panel).

    Parameters
    ----------
    explanation_style:
        "simple" → 1 sentence (quality only, or eval if quality unknown).
        None / "intermediate" / "advanced" → 2 sentences (quality + eval).
    """
    eval_info = engine_signal.get("evaluation", {})
    band = eval_info.get("band", "equal")
    side = eval_info.get("side", "unknown")
    eval_type = eval_info.get("type", "cp")
    move_quality = engine_signal.get("last_move_quality", "unknown")

    style = explanation_style if explanation_style in ("simple", "advanced") else "intermediate"

    # Urgency prefix (critical positions only)
    urgency = compute_urgency(engine_signal)
    urgency_prefix = "Attention: " if urgency == "critical" else ""

    # Move quality comment (primary — about what the human just did)
    quality_by_style: dict[str, str] = _QUALITY_COMMENT.get(move_quality, {})
    quality_comment = quality_by_style.get(style, quality_by_style.get("intermediate", ""))

    # Evaluation context sentence (plain, used by simple style)
    phase = engine_signal.get("phase", "")
    if eval_type == "mate":
        # "forced" satisfies validate_mode_2_semantic's mate-decisiveness gate
        # ("inevitable" or "forced" required when eval_type == 'mate').
        # Avoids the FORBIDDEN_ENGINE_SPECULATION token "engine".
        eval_sentence = f"This is a forced mate — {side} secures the decisive outcome."
    elif band == "equal":
        # Pre-Sprint-5.A wording read "The engine evaluation is equal." —
        # "engine" is on validate_mode_2_semantic's
        # FORBIDDEN_ENGINE_SPECULATION list and would now reject the
        # response at the boundary.  Drop the "engine" prefix but keep
        # the load-bearing token "equal" (still pinned by
        # test_cp_equal_produces_equal_in_hint and the band-word check
        # in test_hint_contains_engine_evaluation_reference).
        eval_sentence = "The evaluation is equal."
    else:
        band_label = _BAND_LABEL.get(band, band.replace("_", " "))
        eval_sentence = f"Position: {side} has {band_label}."

    if style == "simple":
        core = quality_comment if quality_comment else eval_sentence
        return urgency_prefix + core

    # For intermediate/advanced: append phase tip to the eval sentence (keeps max-2-sentence
    # constraint since quality_comment + eval_with_tip = 2 items total).
    phase_tip = _PHASE_TIP.get(phase, "") if eval_type != "mate" else ""
    if phase_tip:
        eval_with_tip = eval_sentence.rstrip(".") + " — " + phase_tip + "."
    else:
        eval_with_tip = eval_sentence

    # Advanced with base_explanation: surface the SafeExplainer detail instead of eval+tip.
    if style == "advanced" and base_explanation:
        parts: list[str] = []
        if quality_comment:
            parts.append(quality_comment)
        parts.append(base_explanation)
        return urgency_prefix + " ".join(parts)

    parts = []
    if quality_comment:
        parts.append(quality_comment)
    parts.append(eval_with_tip)
    return urgency_prefix + " ".join(parts)


# ---------------------------------------------------------------------------
# LLM hint builder
# ---------------------------------------------------------------------------


def _derive_player_color(fen: str) -> str:
    """Return ``"white"`` or ``"black"`` for the player who just moved.

    The Mode-1 hint addresses the human (``you``).  Without knowing
    whether that ``you`` is White or Black, the LLM has no way to tell
    whether ``engine_signal.side == "white"`` means the player is
    winning or losing — and the production probe on 2026-05-10 showed
    DeepSeek defaulting to a wrong "you have a decisive advantage"
    when the player was actually Black being mated on f7.

    The FEN after a move has the OPPONENT's side-to-move flag, so the
    player's colour is the opposite of FEN field 2.  ``"startpos"`` and
    malformed FENs return ``"unknown"`` so the renderer can fall back
    to the side-neutral framing.
    """
    if not fen or fen.strip().lower() == "startpos":
        return "unknown"
    try:
        parts = fen.split()
        if len(parts) < 2:
            return "unknown"
        side_to_move = parts[1].lower()
        if side_to_move == "w":
            return "black"
        if side_to_move == "b":
            return "white"
        return "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _build_hint_llm(
    engine_signal: dict,
    explanation_style: str | None,
    fen: str,
) -> str:
    """Generate a coaching hint via the LLM (Mode-1 system prompt).

    Raises on any failure so the caller can fall back to _build_hint().
    """
    rag_docs = _retrieve(engine_signal, _DOCS)
    player_color = _derive_player_color(fen)
    prompt = render_mode_1_prompt(
        system_prompt=SYSTEM_PROMPT_MODE_1,
        engine_signal=engine_signal,
        fen=fen,
        explanation_style=explanation_style,
        rag_docs=rag_docs,
        player_color=player_color,
    )
    response = _call_llm(prompt).strip()
    if not response:
        raise ValueError("Empty LLM response")

    # Defense-in-depth: every Mode-2 gate the API boundary will re-run
    # at `validate_live_move_response` must run here too, so a borderline
    # LLM hint is caught inside the retry loop and either retried or
    # falls through to the deterministic `_build_hint` — never escapes
    # this function and 500s the route.
    #   - _check_output    → prompt-leak / bypass / identity / PII / harmful
    #   - _validate_neg    → forbidden chess vocabulary (engine, calculate,
    #                        algebraic moves, ...)
    #   - _validate_struct → advisory sections (recommended move, plan,
    #                        "white can", consider, ...)
    #   - _validate_sem    → equal-band words (slight advantage, better,
    #                        initiative), mate-decisiveness, invented
    #                        tactics when tactical_flags == []
    # Each raises a specific exception type; the retry loop in
    # `generate_live_reply` catches all of them under its broad
    # `except Exception` and retries / falls back.
    _check_output(response)
    _validate_neg(response)
    _validate_struct(response)
    _validate_sem(response, engine_signal)
    return response


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_live_reply(
    fen: str,
    uci: str,
    player_id: str = "demo",
    explanation_style: str | None = None,
    stockfish_json: dict | None = None,
    force_deterministic: bool = False,
) -> LiveMoveReply:
    """Generate a coaching hint for the human's move.

    Attempts the LLM path first (full Mode-1 pipeline); falls back to the
    deterministic _build_hint() when Ollama is unavailable or returns an
    empty / invalid response.

    Parameters
    ----------
    fen :
        Board position after the human's move (FEN string or "startpos").
    uci :
        The human's move in UCI notation (e.g. "e2e4", "e7e8q").
    player_id :
        Player identifier — not reflected in the engine signal.
    explanation_style :
        "simple" (beginner), "intermediate" (default), or "advanced".
    force_deterministic :
        Skip the LLM path entirely and emit the deterministic hint.
        Used by the /live/move handler's safety net when the boundary
        validator rejects an LLM-pipeline hint (`ExplainSchemaError`).
        The deterministic builder is constructed to satisfy every
        Mode-2 gate by construction, so this re-call cannot 500 the
        route on the same validator drift.

    Returns
    -------
    LiveMoveReply
        hint           — 1-2 sentence coaching feedback.
        engine_signal  — from extract_engine_signal(); never from user input.
        move_quality   — engine's last_move_quality or "unknown".
        mode           — always "LIVE_V1".
    """
    engine_signal = extract_engine_signal(stockfish_json or {}, fen=fen)
    move_quality = engine_signal.get("last_move_quality", "unknown")

    # --- LLM path with retry ---
    if _LLM_AVAILABLE and not force_deterministic:
        for attempt in range(_LIVE_MAX_RETRIES + 1):
            if attempt > 0:
                time.sleep(_LIVE_RETRY_DELAY_SECONDS)
            try:
                hint = _build_hint_llm(engine_signal, explanation_style, fen)
                if not hint.strip():
                    raise ValueError("Empty hint from LLM")
                return LiveMoveReply(
                    hint=hint,
                    engine_signal=engine_signal,
                    move_quality=move_quality,
                    mode="LIVE_V1",
                )
            except Exception as exc:  # noqa: BLE001
                remaining = _LIVE_MAX_RETRIES - attempt
                if remaining > 0:
                    # Per-attempt retry — kept at DEBUG since the
                    # next iteration may succeed.  Only the
                    # exhausted-retries path needs operator attention.
                    logger.debug("Mode-1 LLM attempt %d failed (%s); retrying", attempt + 1, exc)
                else:
                    # All attempts exhausted — production-impacting:
                    # Ollama unreachable, model not pulled, etc.  See
                    # the matching WARNING in chat_pipeline.py for
                    # rationale; both pipelines should surface the
                    # same signal in production logs.
                    logger.warning(
                        "Mode-1 LLM failed after %d attempts (%s: %s); using deterministic fallback",
                        attempt + 1,
                        type(exc).__name__,
                        exc,
                    )

    # --- Deterministic fallback ---
    base_explanation = _safe_explainer.explain(engine_signal)
    hint = _build_hint(uci, engine_signal, base_explanation, explanation_style=explanation_style)
    return LiveMoveReply(
        hint=hint,
        engine_signal=engine_signal,
        move_quality=move_quality,
        mode="LIVE_V1",
    )
