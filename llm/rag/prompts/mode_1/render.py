"""
Mode-1 prompt renderer.

Builds the full LLM prompt for per-move quick coaching feedback.
The system prompt instructs the model to respond in 1-2 sentences,
adapting language to the player's skill level.
"""

from __future__ import annotations

import json


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

    # Optional RAG context (brief — Mode-1 is short)
    rag_block = ""
    if rag_docs:
        snippets = [d["content"]["description"][:120] for d in rag_docs[:2]]
        rag_block = "\n\nREFERENCE CONTEXT:\n" + "\n".join(f"• {s}" for s in snippets)

    safe_fen = "".join(c if c >= "\x20" else " " for c in fen)

    prompt = f"""{system_prompt}

────────────────────────────
POSITION CONTEXT
────────────────────────────
FEN: {safe_fen}
Player level: {level}
Move quality: {move_quality}
Engine evaluation: {eval_desc}
Game phase: {phase}
Engine signal (structured):
{json.dumps(engine_signal, indent=2)}{rag_block}

────────────────────────────
TASK
────────────────────────────
Provide your 1–2 sentence coaching feedback for the move just played:"""

    return prompt.strip()
