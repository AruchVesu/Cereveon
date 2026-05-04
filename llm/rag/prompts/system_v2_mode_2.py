from pathlib import Path

SYSTEM_PROMPT = Path(Path(__file__).with_suffix(".txt")).read_text(encoding="utf-8")
