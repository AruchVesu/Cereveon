from pathlib import Path

ROOT = Path("memory/users")
ROOT.mkdir(parents=True, exist_ok=True)


def save_memory(user_id: str, memory):
    path = ROOT / f"{user_id}.json"
    path.write_text(memory.model_dump_json(indent=2))
