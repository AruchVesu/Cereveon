# llm/confidence_language_controller.py


def compute_confidence(esv: dict) -> str:
    if esv["evaluation"]["type"] == "mate":
        return "high"

    if esv["eval_delta"] in ("increase", "decrease"):
        return "medium"

    return "low"


def compute_urgency(esv: dict) -> str:
    if "forced_mate" in esv.get("tactical_flags", []):
        return "critical"

    if esv["last_move_quality"] == "blunder":
        return "critical"

    if esv["evaluation"]["band"] in ("decisive_advantage", "clear_advantage"):
        return "caution"

    return "calm"


def compute_tone(player_elo: int | None, confidence: str, urgency: str) -> str:
    if urgency == "critical":
        return "urgent"

    if player_elo and player_elo < 1200:
        return "supportive"

    if confidence == "low":
        return "instructive"

    return "neutral"


# ---------------------------------------------------------
# STYLE INJECTION INTO PROMPT
# ---------------------------------------------------------


def build_style_instruction(tone: str, urgency: str, confidence: str) -> str:
    tone_map = {
        "supportive": "Use encouraging and simple language.",
        "neutral": "Use clear and objective coaching language.",
        "instructive": "Explain ideas step-by-step in a teaching tone.",
        "urgent": "Use concise and urgent wording highlighting immediate danger.",
    }

    urgency_map = {
        "calm": "Maintain a calm explanatory tone.",
        "caution": "Indicate practical concern without alarm.",
        "critical": "Stress immediacy and seriousness of the position.",
    }

    confidence_map = {
        "high": "Speak with clarity and certainty.",
        "medium": "Use balanced and careful phrasing.",
        "low": "Use cautious and probabilistic wording.",
    }

    return (
        "LANGUAGE STYLE RULES:\n"
        f"- {tone_map[tone]}\n"
        f"- {urgency_map[urgency]}\n"
        f"- {confidence_map[confidence]}\n"
    )


# ---------------------------------------------------------
# MAIN ENTRY
# ---------------------------------------------------------


def build_language_controller_block(esv: dict, player_elo: int | None = None) -> str:
    confidence = compute_confidence(esv)
    urgency = compute_urgency(esv)
    tone = compute_tone(player_elo, confidence, urgency)

    return build_style_instruction(tone, urgency, confidence)
