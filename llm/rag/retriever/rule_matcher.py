# rag/retriever/rule_matcher.py


def matches_conditions(esv: dict, conditions: dict) -> bool:
    for path, expected in conditions.items():
        parts = path.split(".")
        current = esv

        for part in parts:
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]

        if isinstance(current, list):
            if expected not in current:
                return False
        else:
            if current != expected:
                return False

    return True
