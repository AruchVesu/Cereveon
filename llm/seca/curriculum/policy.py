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
        """Map a curriculum ``topic`` to a concrete exercise type.

        ``topic`` arrives from two vocabularies that BOTH reach this method,
        so the mapping must cover both:

        * primary game-history path — ``_CATEGORY_TO_TOPIC`` in
          ``generator.py`` emits ``tactics`` / ``endgame`` / ``opening``;
        * skill-vector fallback — ``choose_topic`` returns the weakest phase
          key (``opening`` / ``middlegame`` / ``endgame``) or, for a player
          with no recorded weaknesses, the default ``opening_principles``.

        Every topic either path can produce is mapped to a specific exercise
        type so a known topic never degrades to the generic ``mixed_training``
        bucket.  (Historical gap: the fallback topics ``middlegame`` and
        ``opening_principles`` fell through to ``mixed_training``, and a dead
        ``time_management`` entry mapped to a ``blitz_simulation`` value no
        path ever emitted.)  ``mixed_training`` is retained ONLY as the
        defensive default for an unrecognised topic.
        """
        mapping = {
            # Primary curriculum topics (game-history dominant category).
            "tactics": "puzzle",
            "opening": "opening_line",
            "endgame": "endgame_drill",
            # Skill-vector fallback vocabulary (phase keys + new-player default).
            "middlegame": "middlegame_plan",
            "opening_principles": "opening_line",
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
