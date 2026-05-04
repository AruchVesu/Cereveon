# seca/inference/pipeline.py
#
# SECA explain endpoint — deterministic implementation using the engine signal
# and SafeExplainer.  Always available (no Ollama dependency).

from __future__ import annotations

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.seca.explainer.safe_explainer import SafeExplainer

_safe_explainer = SafeExplainer()


async def explain_position(fen: str) -> dict:
    """Return a structured position explanation for the given FEN.

    Uses SafeExplainer (deterministic, always available).  Engine signal is
    extracted from the FEN — never taken from user input.
    """
    engine_signal = extract_engine_signal({}, fen=fen)
    explanation = _safe_explainer.explain(engine_signal)
    return {
        "explanation": explanation,
        "engine_signal": engine_signal,
    }
