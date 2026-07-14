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
            # "best move" is a FORBIDDEN_PATTERNS bigram (`\bbest move\b`,
            # PR #284) — the advanced-style /live/move deterministic hint
            # embeds these strings verbatim, and the boundary re-validator
            # 500s on a lexical hit (the PR #131/#132 parity class).  Keep
            # every entry clear of the Mode-2 lexical gate; the sweep in
            # test_live_move_pipeline.py::TestFallbackGateSafetySweep pins it.
            "beginner": "That was the strongest choice — well played!",
            "intermediate": "That was the strongest choice.",
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

    def explain(
        self,
        engine_signal: Dict,
        skill_level: str = "intermediate",
        player_color: str = "unknown",
    ) -> str:
        level = (
            skill_level
            if skill_level in ("beginner", "intermediate", "advanced")
            else "intermediate"
        )
        parts: list[str] = []

        eval_info = engine_signal.get("evaluation", {})
        eval_type = eval_info.get("type", "cp")
        band = eval_info.get("band", "equal")
        side = eval_info.get("side", "unknown")
        last_quality = engine_signal.get("last_move_quality", "unknown")

        # 1) Evaluation
        if eval_type == "mate":
            # See PR #167: "force(d) mate" trips MATE_CLAIM_PATTERNS in
            # the Mode-2 lexical filter; "Mate is inevitable" satisfies
            # the matching MATE_INEVITABILITY_SEMANTIC require without
            # tripping the lexical forbid.  PR #167 fixed this in the
            # two coach pipelines; this is the third call site that PR
            # missed — surfaced via the chat-deterministic-fallback path
            # which routes through SafeExplainer.
            #
            # When the caller supplies the player's colour (the Mode-1
            # advanced-style hint passes it; neutral callers — /analyze,
            # the inference pipeline — leave it "unknown"), frame the
            # winner in the second person to match the coach pipelines.
            # Only the subject noun changes, so the carrier still clears
            # both gates.  Case-insensitive; unknown colour OR side keeps
            # the third person.
            side_l = side.lower() if isinstance(side, str) else ""
            color_l = player_color.lower() if isinstance(player_color, str) else ""
            if color_l in ("white", "black") and side_l in ("white", "black"):
                if side_l == color_l:
                    parts.append("Mate is inevitable — you are winning.")
                else:
                    parts.append("Mate is inevitable — your opponent is winning.")
            elif side_l in ("white", "black"):
                parts.append(f"Mate is inevitable — {side} is winning.")
            else:
                parts.append("Mate is inevitable — the decisive outcome is sealed.")
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
