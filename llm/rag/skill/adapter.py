from llm.rag.skill.rules import SKILL_RULES
from llm.rag.skill.levels import SkillLevel


def build_skill_block(level: SkillLevel) -> str:
    rules = SKILL_RULES[level.value]

    return f"""
SKILL ADAPTATION (MANDATORY):

Target player level: {level.value}

Tone: {rules['tone']}
Focus: {rules['focus']}

Constraints:
- Maximum sentences: {rules['max_sentences']}
- Chess jargon allowed: {rules['allow_jargon']}

Guidelines:
- Do not exceed sentence limit.
- Match explanation depth to the skill level.
- Keep clarity higher than complexity.
"""
