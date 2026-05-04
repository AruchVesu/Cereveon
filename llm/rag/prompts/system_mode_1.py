from pathlib import Path

SYSTEM_PROMPT_MODE_1 = Path(Path(__file__).with_suffix(".txt")).read_text(encoding="utf-8")
