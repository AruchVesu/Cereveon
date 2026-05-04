def confidence_level(engine_signal: dict) -> str:
    evaluation = engine_signal.get("evaluation", {})
    band = evaluation.get("band")
    eval_type = evaluation.get("type")

    if eval_type == "mate":
        return "decisive"

    if band == "decisive_advantage":
        return "high"

    if band in ("clear_advantage", "small_advantage"):
        return "moderate"

    if band == "equal":
        return "neutral"

    return "uncertain"
