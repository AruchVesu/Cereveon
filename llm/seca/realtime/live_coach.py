class LiveCoach:
    """
    Real-time adaptive coaching during a live game.
    Runs once per move.
    """

    def __init__(
        self,
        move_analyzer,
        skill_updater,
        hint_policy,
        tone_adapter,
        opponent_controller,
        coach_llm,
    ):
        self.move_analyzer = move_analyzer
        self.skill_updater = skill_updater
        self.hint_policy = hint_policy
        self.tone_adapter = tone_adapter
        self.opponent_controller = opponent_controller
        self.coach_llm = coach_llm

    # ---------------------------------------------------------

    def on_player_move(self, player_id: str, board, move):
        """
        Called immediately after the player makes a move.
        """

        # 1. Analyze move quality
        analysis = self.move_analyzer.evaluate(board, move)

        # 2. Update skill belief instantly
        new_skill = self.skill_updater.update_realtime(player_id, analysis)

        # 3. Decide whether to intervene
        decision = self.hint_policy.decide(new_skill, analysis)

        # 4. Adapt opponent difficulty continuously
        self.opponent_controller.adjust(new_skill)

        # 5. Possibly generate explanation
        if decision["give_hint"]:
            tone = self.tone_adapter.select(new_skill, analysis)

            explanation = self.coach_llm.explain_realtime(
                analysis=analysis,
                tone=tone,
            )
        else:
            explanation = None

        return {
            "skill": new_skill.tolist(),
            "analysis": analysis,
            "hint": explanation,
        }
