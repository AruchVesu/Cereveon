"""
Mode-1 prompt renderer.

Builds the full LLM prompt for per-move quick coaching feedback.
The system prompt instructs the model to respond in 1-2 sentences,
adapting language to the player's skill level.
"""

from __future__ import annotations

import json

from llm.rag.prompts.engine_facts import describe_threats, render_engine_facts


_BAND_LABEL: dict[str, str] = {
    "equal": "equal",
    "small_advantage": "a small advantage",
    "clear_advantage": "a clear advantage",
    "decisive_advantage": "a decisive advantage",
}

_STYLE_TO_LEVEL: dict[str, str] = {
    "simple": "beginner",
    "intermediate": "intermediate",
    "advanced": "advanced",
}


def render_mode_1_prompt(
    *,
    system_prompt: str,
    engine_signal: dict,
    fen: str,
    explanation_style: str | None,
    rag_docs: list[dict] | None = None,
    player_color: str = "unknown",
    last_move_phrase: str = "",
    last_move_uci: str = "",
) -> str:
    """Build the Mode-1 LLM prompt.

    Parameters
    ----------
    system_prompt:
        Full Mode-1 system prompt text.
    engine_signal:
        Structured engine signal from extract_engine_signal().
    fen:
        Current board position (FEN string).
    explanation_style:
        One of "simple", "intermediate", "advanced" (or None → intermediate).
    rag_docs:
        Optional retrieved coaching documents for contextual grounding.
    player_color:
        ``"white"`` or ``"black"`` — the colour the player is playing.
        Derived by the caller from the FEN's side-to-move flag (which
        points to the OPPONENT after the player has moved).  Used to
        pre-frame the evaluation from the player's perspective so the
        LLM doesn't have to compare ``engine_signal.side`` to the
        player's colour itself — when it had to guess, DeepSeek
        defaulted to "you have a decisive advantage" even when the
        player was losing (production probe 2026-05-10).  Defaults to
        ``"unknown"`` for backwards compatibility, which falls back to
        the side-neutral phrasing.
    last_move_uci:
        The player's move in UCI (e.g. ``"f3g5"``).  Used to describe what
        the move attacks (``describe_threats``) in the ENGINE FACTS block.
        Empty → no threat line (back-compat for callers that don't supply it).
    """
    level = _STYLE_TO_LEVEL.get(explanation_style or "intermediate", "intermediate")

    evaluation = engine_signal.get("evaluation", {})
    band = evaluation.get("band", "equal")
    side = evaluation.get("side", "unknown")
    eval_type = evaluation.get("type", "cp")
    phase = engine_signal.get("phase", "middlegame")
    move_quality = engine_signal.get("last_move_quality", "unknown")

    if eval_type == "mate":
        eval_desc = f"forced mate — {side} is winning"
    else:
        band_label = _BAND_LABEL.get(band, band.replace("_", " "))
        eval_desc = f"{side} has {band_label}"

    # Pre-framed player perspective.  The LLM was guessing wrong on
    # this — see the docstring above and PR #88 for the production
    # incident.  We do the side comparison here in deterministic code
    # so the LLM only has to repeat it, not derive it.
    player_perspective = _frame_player_perspective(
        eval_type=eval_type,
        band=band,
        side=side,
        player_color=player_color,
    )

    # Optional RAG context (brief — Mode-1 is short)
    rag_block = ""
    if rag_docs:
        snippets = [d["content"]["description"][:120] for d in rag_docs[:2]]
        rag_block = "\n\nREFERENCE CONTEXT:\n" + "\n".join(f"• {s}" for s in snippets)

    safe_fen = "".join(c if c >= "\x20" else " " for c in fen)
    move_line = (
        f"\nLast move played by the player: {last_move_phrase}." if last_move_phrase else ""
    )

    # Authoritative engine-grounding block — mirrors the Mode-2 chat ENGINE
    # FACTS block (PRs #253/#254).  The tactical/positional flag facts plus what
    # the last move attacks anchor the LLM so it can't invent threats / pins /
    # forks / "hanging" pieces that aren't on the board (the Mode-1 complex-
    # position hallucination class).  The eval is omitted (include_eval=False):
    # it's already framed above in POSITION CONTEXT.  ``player_color`` flips the
    # flag perspective so a Black player isn't handed the opponent's facts.
    # ``include_check=False`` drops the transient check fact: Mode-1 coaches the
    # post-move position, but the engine's forced reply resolves any check the
    # player just gave, so by the time the hint is read the king is no longer in
    # check — surfacing it produced a phantom "opponent's king is in check".
    fact_lines = render_engine_facts(
        engine_signal, player_color=player_color, include_eval=False, include_check=False
    )
    threat = describe_threats(fen, last_move_uci) if last_move_uci else ""
    if threat:
        fact_lines.append(threat[0].upper() + threat[1:])
    facts_block = ""
    if fact_lines:
        facts_block = (
            "\n\nENGINE FACTS (authoritative — base every tactical or evaluative "
            "claim ONLY on these and the move above; do NOT invent threats, pins, "
            "forks, or mates that aren't listed):\n- " + "\n- ".join(fact_lines)
        )

    # When move quality is known (the client sent the pre-move FEN, so the
    # handler graded the before->after eval swing), instruct the LLM to judge
    # the MOVE by that grade rather than by who is currently ahead — this is the
    # move-blame fix: a good move played in a worse position is still good.
    # Dormant when quality is "unknown" (older clients / no pre-move FEN).
    quality_guidance = ""
    if move_quality and move_quality != "unknown":
        quality_guidance = (
            '\nThe "Move quality" line grades the move the player just made. '
            "Judge the move by THAT — a good move can be played in a worse "
            "position. Only call it a mistake or blunder when the move quality "
            "says so, not because the opponent is ahead."
        )

    prompt = f"""{system_prompt}

────────────────────────────
POSITION CONTEXT
────────────────────────────
FEN: {safe_fen}
Player level: {level}
Player colour: {player_color}{move_line}
Move quality: {move_quality}
Engine evaluation (neutral): {eval_desc}
After the player's move: {player_perspective}
Game phase: {phase}
Engine signal (structured):
{json.dumps(engine_signal, indent=2)}{rag_block}{facts_block}

────────────────────────────
TASK
────────────────────────────
Provide your 1–2 sentence coaching feedback for the move just played.
When you say "you" or "your", it refers to the player (colour shown
above).  Use the "After the player's move" line as the authoritative
framing for whose position improved or worsened — do NOT re-derive
which colour is ahead from the structured engine_signal.side field.{quality_guidance}"""

    return prompt.strip()


def _frame_player_perspective(
    *,
    eval_type: str,
    band: str,
    side: str,
    player_color: str,
) -> str:
    """Pre-compute the player-perspective evaluation line.

    Returns a single phrase like ``"you have a decisive advantage"``,
    ``"your opponent has a decisive advantage"``, or ``"the position is
    equal"``.  When either side info is unknown, falls back to the
    side-neutral neutral phrasing so the LLM at least sees the raw
    engine fact.
    """
    if eval_type == "mate":
        if player_color in ("white", "black") and side in ("white", "black"):
            if side == player_color:
                return "you are about to deliver mate"
            return "you are about to be mated"
        return f"forced mate — {side} is winning"

    if band == "equal":
        return "the position is equal"

    band_label = _BAND_LABEL.get(band, band.replace("_", " "))

    if player_color not in ("white", "black") or side not in ("white", "black"):
        # No reliable perspective comparison — fall back to side-neutral.
        return f"{side} has {band_label}"

    if side == player_color:
        return f"you have {band_label}"
    return f"your opponent has {band_label}"
