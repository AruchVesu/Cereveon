# rag/retriever/priority.py

PRIORITY_ORDER = [
    "evaluation_translation",
    "move_quality",
    "tactical",
    "positional",
    "phase_guidance",
]

PRIORITY_MAP = {name: idx for idx, name in enumerate(PRIORITY_ORDER)}
