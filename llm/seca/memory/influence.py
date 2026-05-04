def build_memory_block(memory) -> str:
    mistakes = [m.tag for m in memory.player.common_mistakes[:3]]

    if not mistakes:
        return ""

    return f"""
PLAYER MEMORY INSIGHT:

Frequent mistake themes:
{", ".join(mistakes)}

Coaching priority:
Address these weaknesses if relevant to the current position.
"""
