import re

FORBIDDEN_EQUAL = [
    r"\bslight advantage\b",
    r"\bbetter\b",
    r"\bwinning\b",
    r"\binitiative\b",
    r"\bpressure\b",
]

FORBIDDEN_ENGINE_SPECULATION = [
    r"\blikely\b",
    r"\bprobably\b",
    r"\bmight\b",
    r"\bengine\b",
    r"\bwants to\b",
]


class Mode2Violation(Exception):
    pass


def validate_mode_2_semantic(text: str, engine_signal: dict) -> None:
    evaluation = engine_signal.get("evaluation", {})
    band = evaluation.get("band")
    eval_type = evaluation.get("type")
    tactical_flags = engine_signal.get("tactical_flags", [])

    lower = text.lower()

    # Equal neutrality
    if band == "equal":
        forbidden = ["slight advantage", "better", "winning", "initiative", "pressure"]
        for word in forbidden:
            if word in lower:
                raise Mode2Violation(f"Equal position described as advantage: '{word}'")

    # Mate decisiveness
    if eval_type == "mate":
        if "inevitable" not in lower and "forced" not in lower:
            raise Mode2Violation("Mate not described as forced/inevitable")

    # Engine speculation
    forbidden_spec = ["likely", "probably", "might", "engine", "wants to"]
    for word in forbidden_spec:
        if word in lower:
            raise Mode2Violation(f"Speculative language detected: '{word}'")

    # Invented tactics
    if not tactical_flags:
        invented = ["fork", "pin", "sacrifice", "attack", "threat"]
        for word in invented:
            if word in lower:
                raise Mode2Violation(f"Invented tactic without flag: '{word}'")
