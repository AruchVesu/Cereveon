def select_plan_type(engine_signal: dict) -> str:
    evaluation = engine_signal.get("evaluation", {})
    eval_type = evaluation.get("type")
    band = evaluation.get("band")
    tactics = engine_signal.get("tactical_flags", [])

    if eval_type == "mate":
        return "forced_mate"

    if "forced_mate" in tactics:
        return "forced_mate"

    if band == "decisive_advantage":
        return "winning"

    if band in ("clear_advantage", "small_advantage"):
        return "advantage"

    if band == "equal":
        return "equal"

    return "unclear"
