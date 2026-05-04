import json
from pathlib import Path
from llm.seca.memory.schema import DialogueMemory

ROOT = Path("memory/users")


def load_memory(user_id: str) -> DialogueMemory:
    path = ROOT / f"{user_id}.json"
    if not path.exists():
        return DialogueMemory(
            player={"skill_estimate": "club", "common_mistakes": [], "strengths": []},
            session={"last_positions": [], "last_topics": []},
        )
    return DialogueMemory.model_validate_json(path.read_text())
