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
    from llm.rag.prompts.move_phrase import describe_move_plain  # type: ignore[import]
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
    # Mode-2 boundary gates run via the shared helper.  See
    # ``llm/seca/coach/_mode_2_validators.py`` for the parity invariant
    # and exception lineage (closes the issue #129 class of bug where a
    # borderline LLM hint escaped the pipeline retry loop and 500'd at
    # ``validate_live_move_response``).
    from llm.seca.coach._mode_2_validators import validate_mode_2_or_raise  # type: ignore[import]
    from llm.rag.validators.mode_2_semantic import Mode2Violation as _Mode2Violation  # type: ignore[import]
    from llm.rag.safety.output_firewall import OutputFirewallError as _OutputFirewallError  # type: ignore[import]
    # Shared targeted-rephrase hints (retry parity with Mode-2 chat +
    # stream, PRs #370/#371/#374): a validator rejection re-asks the LLM
    # with a hint naming the exact offending token instead of repeating
    # the identical prompt until exhaustion.  chat_stream_pipeline imports
    # the same helper; chat_pipeline has no import back into this module,
    # so the dependency is acyclic.
    from llm.seca.coach.chat_pipeline import _targeted_retry_hint  # type: ignore[import]
    _LLM_AVAILABLE = True
except Exception as _llm_import_exc:  # noqa: BLE001
    logger.warning("LLM imports unavailable — deterministic path only: %s", _llm_import_exc)
    _LLM_AVAILABLE = False

#: Retry budget — sourced from ``llm.rag.llm.config.MAX_MODE_2_RETRIES``
#: so the four LLM-bearing pipelines stay in lock-step.  PR 11
#: (2026-05-15) consolidated.  Local alias preserved so a reader
#: grepping for "live retry budget" lands on this module.
from llm.rag.llm.config import (
    MAX_MODE_2_RETRIES as _CONFIG_MAX_RETRIES,
    MODE_1_MAX_COMPLETION_TOKENS as _MODE_1_MAX_COMPLETION_TOKENS,
)

_LIVE_MAX_RETRIES = _CONFIG_MAX_RETRIES
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
    #
    # ``best move`` (the two-word phrase) joined mode_2_negative's
    # ENGINE_LEXICAL_PATTERNS via PR #284 (2026-06-22) — AFTER this table
    # was written.  The original ``best`` entries opened with "Best move —"
    # and were rejected at the live boundary re-validator whenever the
    # deterministic path served a best-graded move (free-tier degraded
    # games, LLM outages); the handler's fallback substitution re-failed
    # the same gate and 500'd the route.  The entries now praise via
    # "strongest choice / continuation".  The gate-safety sweep in
    # test_live_move_pipeline.py pins every entry x style x band through
    # all three boundary gates so the next lexical-gate addition is loud.
    "blunder": {
        # One sentence (was two): simple-style hints append the mandatory
        # mate sentence when eval_type == "mate", and a two-sentence
        # quality comment would push the composed hint past the
        # 1-2 sentence Mode-1 contract.
        "simple": "Oops — that was a blunder, a piece was left unprotected.",
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
        "intermediate": "That was the strongest choice available.",
        "advanced": "The strongest continuation — precisely played.",
    },
}

# Praise variants for a player who is CLEARLY WORSE — the opponent holds a
# clear/decisive advantage or has inevitable mate.  The relative move grade
# is honest: in a difficult (e.g. zugzwang) position the least-bad move
# still grades "best", because the engine's pre-move eval already prices in
# the forced deterioration.  But the standard praise above reads as if the
# player stood well ("maintains a solid position" while the opponent holds
# a decisive advantage) — right about the move, wrong about the whole
# context (user report, 2026-07-10).  These variants keep crediting the
# move (the move-blame fix stays intact: a good move in a lost position is
# still called good) while carrying the whole-position context in the same
# sentence.  Small advantages deliberately do NOT trigger them — tempering
# praise over a fraction-of-a-pawn edge would be move-blame in reverse.
_QUALITY_COMMENT_DIFFICULT: dict[str, dict[str, str]] = {
    "good": {
        "simple": "Nice move — you're still in a tough spot, though.",
        "intermediate": (
            "Good move — one of your stronger options, though the position stays difficult."
        ),
        "advanced": "Good move — a sound choice, though the position remains difficult.",
    },
    "excellent": {
        "simple": "Well spotted — the position is still tough, though.",
        "intermediate": "Excellent choice under pressure — the position stays difficult.",
        "advanced": (
            "Excellent — among the strongest continuations, though the position remains difficult."
        ),
    },
    "best": {
        "simple": "Good find — that was your strongest option, but you're still in a tough spot.",
        "intermediate": "That was your strongest option, though the position stays difficult.",
        "advanced": "The strongest continuation available — though the position remains difficult.",
    },
}


def _player_clearly_worse(eval_type: str, band: str, side: str, player_color: str) -> bool:
    """True when the player is clearly worse: the OPPONENT holds a
    clear/decisive advantage or has inevitable mate.

    Requires both colours to be known — side-neutral callers (unknown
    player colour, degraded signals) keep the standard praise.  Small
    advantages don't count; see the ``_QUALITY_COMMENT_DIFFICULT``
    rationale above.
    """
    side_l = side.lower() if isinstance(side, str) else ""
    color_l = player_color.lower() if isinstance(player_color, str) else ""
    if color_l not in ("white", "black") or side_l not in ("white", "black"):
        return False
    if side_l == color_l:
        return False
    return eval_type == "mate" or band in ("clear_advantage", "decisive_advantage")

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
    player_color: str = "unknown",
) -> str:
    """Deterministic 1-2 sentence coaching hint (LLM fallback).

    Leads with move-quality feedback (most relevant to the player), then
    appends a single evaluation context sentence.  Phase-specific tips are
    intentionally absent — they belong in Mode 2 (the chat / LLM panel).

    Parameters
    ----------
    explanation_style:
        "simple" → 1 sentence (quality only, or eval if quality unknown);
        2 sentences in mate positions, where the inevitability sentence
        is mandatory (semantic gate REQUIRE).
        None / "intermediate" / "advanced" → 2 sentences (quality + eval).
    player_color:
        ``"white"`` / ``"black"`` — the colour the player is playing
        (derived by the caller from the post-move FEN).  When known, the
        mate eval sentence is framed in the second person ("you" / "your
        opponent") to match the LLM path's ``_frame_player_perspective``;
        a winning player should not be told a detached third-person
        "white secures the decisive outcome".  Defaults to ``"unknown"``,
        which preserves the side-named third-person phrasing.
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

    # Move quality comment (primary — about what the human just did).
    # Praise grades switch to the "difficult position" variants when the
    # player is clearly worse: the grade is relative to the alternatives
    # (in zugzwang the least-bad move still grades "best"), so bare
    # celebration would be right about the move but wrong about the
    # whole context.
    quality_table = _QUALITY_COMMENT
    if move_quality in _QUALITY_COMMENT_DIFFICULT and _player_clearly_worse(
        eval_type, band, side, player_color
    ):
        quality_table = _QUALITY_COMMENT_DIFFICULT
    quality_by_style: dict[str, str] = quality_table.get(move_quality, {})
    quality_comment = quality_by_style.get(style, quality_by_style.get("intermediate", ""))

    # Evaluation context sentence (plain, used by simple style)
    phase = engine_signal.get("phase", "")
    if eval_type == "mate":
        # "inevitable" satisfies validate_mode_2_semantic's mate-decisiveness
        # gate (the gate accepts EITHER "inevitable" OR "forced" when
        # eval_type == 'mate').  We MUST NOT use the two-word phrase
        # "forced mate" because that matches MATE_CLAIM_PATTERNS in
        # mode_2_negative — the lexical filter rejects the exact regex
        # `\bforce(?:d)? mate\b` regardless of engine corroboration.
        # The two validators previously contradicted each other: semantic
        # required "forced" or "inevitable" anywhere, lexical forbade
        # "forced mate" as a phrase.  Saying "Mate is inevitable" threads
        # the needle — single-word "inevitable" satisfies the require,
        # no "force(d) mate" adjacent pair triggers the lexical reject.
        # Prior phrasing 500'd /live/move on every mate-ending game
        # because the boundary re-validator at server.py:1357 rejected
        # the fallback and the handler intentionally surfaces that as
        # a "structural bug" (and it was).  Caught on-device 2026-05-15.
        #
        # Frame from the player's perspective when the colour is known:
        # "you" when the player is the mating side, "your opponent" when
        # being mated — mirrors the LLM path's _frame_player_perspective so
        # a winning player isn't told a detached "white secures …".  Only
        # the subject noun changes; the proven "Mate is inevitable — …
        # secures the decisive outcome" carrier (single-word "inevitable"
        # satisfies the semantic require, no "force(d) mate" pair) is kept
        # verbatim so the gate-safety pins in test_deterministic_mate_
        # phrasing.py still hold.  Compared case-insensitively because the
        # render fixtures use "White" while extract_engine_signal emits
        # lowercase; unknown colour OR side falls back to the third person.
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
        # Pre-Sprint-5.A wording read "The engine evaluation is equal." —
        # "engine" is on validate_mode_2_semantic's
        # FORBIDDEN_ENGINE_SPECULATION list and would now reject the
        # response at the boundary.  The interim phrasing kept
        # ``evaluation`` as the load-bearing word but users perceived
        # it as the coach quoting engine output rather than offering
        # an assessment.  The current phrasing keeps the ``equal``
        # token (still pinned by tests) while reading as a plain
        # position description.
        eval_sentence = "The position is roughly equal."
    else:
        band_label = _BAND_LABEL.get(band, band.replace("_", " "))
        # Player-seat framing (de-robotified 2026-07-09, mirroring the
        # Mode-2 fallback warm in PR #372): the old "Position: {side} has
        # {band}." read as a labeled engine readout and ignored the
        # player_color the caller already derives — a Black player was
        # told "white has a clear advantage" in the detached third
        # person.  Unknown colour OR side keeps a capitalised side name.
        # Band vocabulary ("advantage") is preserved for the phrasing
        # pins; stays one sentence for the max-2-sentence contract.
        side_l = side.lower() if isinstance(side, str) else ""
        color_l = player_color.lower() if isinstance(player_color, str) else ""
        if color_l in ("white", "black") and side_l in ("white", "black"):
            subject = "You have" if side_l == color_l else "Your opponent has"
            eval_sentence = f"{subject} {band_label}."
        else:
            side_label = side.capitalize() if isinstance(side, str) and side else side
            eval_sentence = f"{side_label} has {band_label}."

    if style == "simple":
        core = quality_comment if quality_comment else eval_sentence
        if eval_type == "mate" and quality_comment:
            # The semantic gate REQUIREs mate decisiveness ("inevitable")
            # in every non-empty hint when eval_type == "mate".  Since the
            # move-quality feature landed, a known grade displaced the mate
            # sentence here, so the simple-style hint ("Perfect move!")
            # failed the boundary re-validator and 500'd the route.  Two
            # sentences stays within the 1-2 sentence Mode-1 contract.
            core = f"{quality_comment} {eval_sentence}"
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
    uci: str,
    retry_hint: str = "",
) -> str:
    """Generate a coaching hint via the LLM (Mode-1 system prompt).

    Raises on any failure so the caller can fall back to _build_hint().

    ``retry_hint`` is the targeted rephrase instruction built from the
    previous attempt's validator exception (``_targeted_retry_hint``).
    Appended AFTER the fully-rendered prompt so it has maximum recency —
    the same end-of-prompt placement the Mode-2 chat retry loop uses.
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
        last_move_phrase=describe_move_plain(fen, uci),
        last_move_uci=uci,
    )
    if retry_hint:
        prompt = prompt + retry_hint
    # Output-spend cap (cost insurance, ~4x the observed 1-2 sentence
    # hint length — see llm.rag.llm.config).  A capped-off outlier fails
    # validation and takes the existing retry → deterministic path.
    response = _call_llm(prompt, max_completion_tokens=_MODE_1_MAX_COMPLETION_TOKENS).strip()
    if not response:
        raise ValueError("Empty LLM response")

    # Defense-in-depth: every Mode-2 gate the API boundary will re-run
    # at `validate_live_move_response` must run here too, so a borderline
    # LLM hint is caught inside the retry loop and either retried or
    # falls through to the deterministic `_build_hint` — never escapes
    # this function and 500s the route.  Gates and their exception
    # types live in ``_mode_2_validators.validate_mode_2_or_raise``;
    # the retry loop in ``generate_live_reply`` catches them all under
    # its broad ``except Exception`` and retries / falls back.  Parity
    # with ``chat_pipeline._build_chat_llm`` is pinned by
    # ``test_validator_parity.py``.
    validate_mode_2_or_raise(response, engine_signal)
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
        retry_hint = ""
        for attempt in range(_LIVE_MAX_RETRIES + 1):
            if attempt > 0:
                time.sleep(_LIVE_RETRY_DELAY_SECONDS)
            try:
                hint = _build_hint_llm(
                    engine_signal, explanation_style, fen, uci, retry_hint=retry_hint
                )
                if not hint.strip():
                    raise ValueError("Empty hint from LLM")
                return LiveMoveReply(
                    hint=hint,
                    engine_signal=engine_signal,
                    move_quality=move_quality,
                    mode="LIVE_V1",
                )
            except _OutputFirewallError:
                # Working-as-intended safety event — never retried
                # (parity with the Mode-2 chat and stream loops).
                logger.debug(
                    "Mode-1 LLM blocked by output firewall; using deterministic fallback"
                )
                break
            except (AssertionError, _Mode2Violation) as exc:
                # Validator rejection — retry with a TARGETED hint naming
                # the exact offending token (retry parity with the Mode-2
                # chat/stream loops, PRs #370/#371/#374) instead of
                # re-sending the identical prompt until exhaustion.
                retry_hint = _targeted_retry_hint(exc)
                remaining = _LIVE_MAX_RETRIES - attempt
                if remaining > 0:
                    logger.debug(
                        "Mode-1 validator rejection on attempt %d (%s); retrying with hint",
                        attempt + 1,
                        exc,
                    )
                else:
                    logger.warning(
                        "Mode-1 LLM failed after %d attempts (%s: %s); using deterministic fallback",
                        attempt + 1,
                        type(exc).__name__,
                        exc,
                    )
            except Exception as exc:  # noqa: BLE001
                remaining = _LIVE_MAX_RETRIES - attempt
                if remaining > 0:
                    # Per-attempt retry — kept at DEBUG since the
                    # next iteration may succeed.  Transport errors keep
                    # Mode-1's historical retry-without-hint behaviour
                    # (a hint can't fix an unreachable provider).
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
    # Derive the player's colour once and thread it through both the
    # SafeExplainer base explanation (surfaced in advanced style) and the
    # hint builder, so the mate phrasing reads in the second person across
    # every style — advanced consumes base_explanation instead of the
    # _build_hint eval sentence, so it needs the colour too.
    player_color = _derive_player_color(fen)
    base_explanation = _safe_explainer.explain(engine_signal, player_color=player_color)
    hint = _build_hint(
        uci,
        engine_signal,
        base_explanation,
        explanation_style=explanation_style,
        player_color=player_color,
    )
    return LiveMoveReply(
        hint=hint,
        engine_signal=engine_signal,
        move_quality=move_quality,
        mode="LIVE_V1",
    )
