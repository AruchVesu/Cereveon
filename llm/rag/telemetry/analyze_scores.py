import json
from collections import Counter
from pathlib import Path

path = Path("telemetry/quality_scores.jsonl")

scores = []
for line in path.read_text().splitlines():
    scores.append(json.loads(line)["score"])

counts = Counter(scores)

print("Score distribution:")
for score in range(0, 11):
    print(f"{score}: {counts.get(score, 0)}")
