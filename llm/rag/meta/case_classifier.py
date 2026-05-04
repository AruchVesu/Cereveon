def infer_case_type(esv: dict) -> str:
    if esv["evaluation"]["type"] == "mate":
        return "forced_mate"

    if esv.get("last_move_quality") in ("mistake", "blunder"):
        return "tactical_mistake"

    if esv.get("position_flags"):
        return "positional_quiet"

    return "general"
