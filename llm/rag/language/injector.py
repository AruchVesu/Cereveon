from llm.rag.language.confidence import confidence_level
from llm.rag.language.tone_rules import TONE_RULES


def inject_confidence_language(prompt: str, engine_signal: dict) -> str:
    level = confidence_level(engine_signal)
    rules = TONE_RULES[level]

    tone_block = f"""
LANGUAGE CONSTRAINTS:

Tone must be: {rules['style']}.

Forbidden words/phrases:
{", ".join(rules["forbidden"]) if rules["forbidden"] else "None"}

Required concepts to reflect when appropriate:
{", ".join(rules["required"]) if rules["required"] else "None"}

These rules are STRICT and override default phrasing.
"""

    return prompt + "\n\n" + tone_block
