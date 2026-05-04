def score_explanation(
    *,
    text: str,
    engine_signal: dict,
) -> int:
    """Score an explanation on a 0..10 scale (higher is better).

    Components (more strict than before):
    - Engine alignment (0-2): mentions the engine band/assessment
    - Causality (0-2): contains causal markers like 'because', 'due to'
    - Completeness (0-2): covers flags from the engine signal
    - Mode-2 discipline (0-2): no advisory/prescriptive words
    - Clarity (0-2): multi-line explanations are preferred

    The function avoids giving small default points for missing signals so that
    a truly weak explanation scores noticeably lower.
    """

    score = 0
    text_lower = text.lower()

    # 1. Engine alignment (0-2)
    eval_info = engine_signal.get("evaluation", {})
    if eval_info:
        band = eval_info.get("band", "")
        # exact band mention is best
        if band and band.replace("_", " ") in text_lower:
            score += 2
        elif band:
            score += 1

    # 2. Causality (0-2)
    causal_markers = [
        "because",
        "due to",
        "explains",
        "reflects",
        "results from",
    ]
    if any(m in text_lower for m in causal_markers):
        score += 2

    # 3. Completeness (0-2): fraction of engine flags covered
    flags = []
    if engine_signal.get("last_move_quality"):
        flags.append("mistake")
    flags += engine_signal.get("tactical_flags", [])
    flags += engine_signal.get("position_flags", [])

    if flags:
        covered = sum(1 for f in flags if f.replace("_", " ") in text_lower)
        ratio = covered / len(flags)
        if ratio >= 0.8:
            score += 2
        elif ratio >= 0.4:
            score += 1

    # 4. Mode-2 discipline (0-2)
    forbidden_soft = ["should", "best move", "consider"]
    if not any(w in text_lower for w in forbidden_soft):
        score += 2

    # 5. Clarity (0-2)
    if len(text.splitlines()) >= 2:
        score += 2
    elif len(text.strip()) > 0:
        score += 1

    return score
