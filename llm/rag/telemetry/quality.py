import json
from datetime import datetime
from pathlib import Path

TELEMETRY_PATH = Path("telemetry/quality_scores.jsonl")
TELEMETRY_PATH.parent.mkdir(exist_ok=True)


def record_quality_score(
    *,
    score: int,
    case_type: str,
    model: str,
    mode: str = "mode_2",
):
    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "score": score,
        "case_type": case_type,
        "model": model,
        "mode": mode,
    }

    with TELEMETRY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
