from llm.rag.skill.adapter import build_skill_block
from llm.rag.skill.levels import SkillLevel


def inject_skill(prompt: str, level: SkillLevel) -> str:
    return prompt + "\n\n" + build_skill_block(level)
