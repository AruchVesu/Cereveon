import json
from pathlib import Path
from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

ROOT = Path(__file__).resolve().parents[2]

with open(ROOT / "tests" / "golden" / "cases" / "tactical_mistake" / "case_001.json", "r") as f:
    case = json.load(f)

esv = extract_engine_signal(case["stockfish_json"])
print(json.dumps(esv, indent=2))
