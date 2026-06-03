class CurriculumPolicy:
    """
    Chooses WHAT to train next.
    """

    def choose_topic(self, skill_vector: dict[str, float]) -> str:
        if not skill_vector:
            return "opening_principles"

        # ``skill_vector`` stores weakness magnitudes written by
        # ``SkillUpdater`` — an EWMA of the per-phase mistake rate where a
        # HIGHER value means a WEAKER area.  The area to train next is the
        # weakest one: the key with the MAX value.  This mirrors
        # ``HistoricalAnalysisPipeline.dominant_category`` (the highest-score
        # category) and ``SkillUpdater``'s dominant-weakness action
        # (``max(weaknesses, ...)``).  Was ``min(...)``, which inverted the
        # semantics and selected the player's STRONGEST area.
        return max(skill_vector, key=lambda k: skill_vector[k])

    # ---------------------------------

    def choose_difficulty(self, rating: float, confidence: float) -> str:
        """
        Adaptive difficulty:
        - high confidence → harder
        - low confidence → easier
        """

        base = rating / 400  # ~0..6

        if confidence < 0.3:
            return "easy"

        if confidence > 0.7 and base > 3:
            return "hard"

        return "medium"

    # ---------------------------------

    def choose_exercise_type(self, topic: str) -> str:

        mapping = {
            "tactics": "puzzle",
            "endgame": "endgame_drill",
            "opening": "opening_line",
            "time_management": "blitz_simulation",
        }

        return mapping.get(topic, "mixed_training")

    # ---------------------------------

    def choose_session_length(self, confidence: float) -> int:
        """
        Minutes.
        """

        if confidence < 0.3:
            return 10

        if confidence > 0.7:
            return 30

        return 20
