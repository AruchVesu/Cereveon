# rag/retriever/retriever.py

from .priority import PRIORITY_MAP
from .rule_matcher import matches_conditions

MAX_DOCS = 7


def retrieve(esv: dict, documents: list) -> list:
    matched = []

    for doc in documents:
        conditions = doc.get("conditions", {})
        if matches_conditions(esv, conditions):
            matched.append(doc)

    matched.sort(key=lambda d: PRIORITY_MAP.get(d.get("type", ""), 99))

    return matched[:MAX_DOCS]
