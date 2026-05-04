class ToneAdapter:
    """
    Chooses explanation tone based on skill + mistake severity.
    """

    def select(self, skill, analysis):
        s = skill[0]
        q = analysis["quality"]

        if s < 0.3:
            return "encouraging_simple"

        if q == "blunder":
            return "calm_warning"

        if s > 0.8:
            return "concise_technical"

        return "neutral"
