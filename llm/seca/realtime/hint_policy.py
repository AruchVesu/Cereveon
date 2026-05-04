class HintPolicy:
    """
    Decides when SECA should speak.
    """

    def decide(self, skill, analysis):
        quality = analysis["quality"]

        # Beginners: help more
        if skill[0] < 0.3:
            give_hint = quality in ("blunder", "mistake", "inaccuracy")

        # Intermediate: only serious errors
        elif skill[0] < 0.7:
            give_hint = quality in ("blunder", "mistake")

        # Advanced: silence unless critical
        else:
            give_hint = quality == "blunder"

        return {"give_hint": give_hint}
