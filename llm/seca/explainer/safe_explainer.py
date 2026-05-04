# llm/seca/explainer/safe_explainer.py

from typing import Dict


class SafeExplainer:
    """
    Deterministic explanation engine.
    Reads the ESV schema produced by extract_engine_signal().
    No LLM.
    """

    _BAND_MESSAGES: dict[str, dict[str, str]] = {
        "equal": {
            "beginner": "The position is about even — both sides have similar chances.",
            "intermediate": "Position is roughly equal.",
            "advanced": "Position is roughly equal.",
        },
        "small_advantage": {
            "beginner": "{side} is doing slightly better right now.",
            "intermediate": "{side} has a slight advantage.",
            "advanced": "{side} has a slight advantage.",
        },
        "clear_advantage": {
            "beginner": "{side} is clearly doing better — their pieces are more active.",
            "intermediate": "{side} has a clear advantage.",
            "advanced": "{side} has a clear advantage.",
        },
        "decisive_advantage": {
            "beginner": "{side} has a very large advantage — the position is almost winning.",
            "intermediate": "{side} has a decisive advantage.",
            "advanced": "{side} has a decisive advantage.",
        },
    }

    _QUALITY_MESSAGES: dict[str, dict[str, str]] = {
        "best": {
            "beginner": "That was the best move — well played!",
            "intermediate": "That was the best move.",
            "advanced": "Excellent move — a top continuation.",
        },
        "excellent": {
            "beginner": "Great move — one of the best options.",
            "intermediate": "Excellent move — a top continuation.",
            "advanced": "Excellent move — a top continuation.",
        },
        "good": {
            "beginner": "Good move — a safe and solid choice.",
            "intermediate": "Good move — a solid choice.",
            "advanced": "Good move — a solid choice.",
        },
        "inaccuracy": {
            "beginner": "That move was not quite the best — a stronger option was available.",
            "intermediate": "Slight inaccuracy — a stronger option existed.",
            "advanced": "Slight inaccuracy — a stronger option existed.",
        },
        "mistake": {
            "beginner": "That move was a mistake — try to avoid this kind of choice.",
            "intermediate": "That was a mistake.",
            "advanced": "That was a mistake.",
        },
        "blunder": {
            "beginner": "That move was a serious mistake — it gave the opponent a big advantage.",
            "intermediate": "That was a blunder.",
            "advanced": "That was a blunder.",
        },
    }

    def explain(self, engine_signal: Dict, skill_level: str = "intermediate") -> str:
        level = skill_level if skill_level in ("beginner", "intermediate", "advanced") else "intermediate"
        parts: list[str] = []

        eval_info = engine_signal.get("evaluation", {})
        eval_type = eval_info.get("type", "cp")
        band = eval_info.get("band", "equal")
        side = eval_info.get("side", "unknown")
        last_quality = engine_signal.get("last_move_quality", "unknown")

        # 1) Evaluation
        if eval_type == "mate":
            parts.append(f"Forced mate — {side} is winning.")
        else:
            band_templates = self._BAND_MESSAGES.get(band, self._BAND_MESSAGES["equal"])
            template = band_templates.get(level, band_templates["intermediate"])
            parts.append(template.format(side=side.capitalize()))

        # 2) Move quality
        quality_map = self._QUALITY_MESSAGES.get(last_quality, {})
        quality_msg = quality_map.get(level, "")
        if quality_msg:
            parts.append(quality_msg)

        return " ".join(parts)
