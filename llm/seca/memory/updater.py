def update_from_engine(memory, esv):
    if "forced_mate" in esv["tactical_flags"]:
        _increment(memory, "missed_forced_mate")

    if esv["last_move_quality"] == "blunder":
        _increment(memory, "blunders")
