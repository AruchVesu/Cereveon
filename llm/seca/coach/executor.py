from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class CoachContent:
    title: str
    description: str
    payload: Dict[str, Any]


class CoachExecutor:
    """
    Converts abstract coach_action → real user content.

    The optional ``game`` argument to ``execute`` carries the
    just-analysed game's summary (rating delta, confidence delta,
    per-phase weakness rates).  When present, the default handler
    synthesises a per-game description from those fields instead of
    the generic ``"Keep playing"`` stub — see PR #173 / the 2026-05-16
    "I see training focus but we actually need to see the full
    information the coach provides" feedback.  When absent (legacy
    callers, unit tests that don't construct a GameSummary), the
    handlers fall back to their pre-PR-#173 canned content.
    """

    def execute(self, action, game=None) -> CoachContent:
        handler = getattr(self, f"_handle_{action.type.lower()}", self._handle_default)
        # All handlers accept an optional ``game`` kwarg.  Pass it
        # through; handlers that don't use it ignore the value.  This
        # keeps the signature symmetrical so any future handler that
        # wants context-aware copy can opt in without another
        # signature change.
        return handler(action, game=game)

    # ---------------- DRILL ----------------

    def _handle_drill(self, action, game=None) -> CoachContent:
        weakness = action.weakness or "general"

        return CoachContent(
            title=f"Targeted drill: {weakness.replace('_', ' ').title()}",
            description="Short focused exercise to improve your weakest skill.",
            payload={
                "duration_min": 10,
                "steps": [
                    "Solve 5 focused positions",
                    "Review mistakes",
                    "Repeat key motif",
                ],
                "weakness": weakness,
            },
        )

    # ---------------- PUZZLES ----------------

    def _handle_puzzle_set(self, action, game=None) -> CoachContent:
        weakness = action.weakness or "tactics"

        return CoachContent(
            title=f"Adaptive puzzle set ({weakness})",
            description="Training positions chosen for your current level.",
            payload={
                "puzzle_count": 5,
                "theme": weakness,
                "rating_offset": -50,
            },
        )

    # ---------------- REFLECT ----------------

    def _handle_reflect(self, action, game=None) -> CoachContent:
        return CoachContent(
            title="Post-game reflection",
            description="Think before the next game to consolidate learning.",
            payload={
                "questions": [
                    "Where was the critical moment?",
                    "What plan did I miss?",
                    "What will I try next game?",
                ]
            },
        )

    # ---------------- REST ----------------

    def _handle_rest(self, action, game=None) -> CoachContent:
        return CoachContent(
            title="Recovery break",
            description="Short rest to prevent fatigue and rating drop.",
            payload={"suggestion": "Take a 10-minute walk and return refreshed."},
        )

    # ---------------- PUZZLE ----------------

    def _handle_puzzle(self, action, game=None) -> CoachContent:
        weakness = action.weakness or "tactics"

        return CoachContent(
            title=f"Puzzle challenge ({weakness})",
            description="Solve this position to sharpen your calculation.",
            payload={
                "puzzle_count": 3,
                "theme": weakness,
                "difficulty": "adaptive",
            },
        )

    # ---------------- PLAN UPDATE ----------------

    def _handle_plan_update(self, action, game=None) -> CoachContent:
        weakness = action.weakness or "general"

        return CoachContent(
            title="Updated training plan",
            description=f"Focus area adjusted: {weakness.replace('_', ' ').title()}.",
            payload={
                "updated_focus": weakness,
                "reason": action.reason or "Based on recent game performance.",
            },
        )

    # ---------------- DEFAULT ----------------

    def _handle_default(self, action, game=None) -> CoachContent:
        """Default action — fired when no controller rule triggered.

        Pre-PR-#173 this returned a generic ``"Keep playing"`` stub
        even when the engine had just analysed the game and we had
        rich data to surface.  When ``game`` (a GameSummary) is
        present, compose a per-game description from rating delta +
        per-phase weakness rates so the user sees what their game
        actually looked like.  Without ``game`` (legacy callers /
        unit tests), keep the canned fallback so the back-compat
        invariants in test_coaching_pipeline_regression hold.
        """
        if game is None:
            return CoachContent(
                title="Keep playing",
                description="No special training needed right now.",
                payload={},
            )

        delta = float(getattr(game, "learning_delta", 0.0) or 0.0)
        weaknesses = dict(getattr(game, "weaknesses", {}) or {})

        # Title bands match the controller's own rule thresholds so
        # the same delta range produces a consistent verdict tone
        # whether or not Rule 1/2 actually fired.
        if delta >= 10.0:
            title = "Strong game"
        elif delta >= 0.0:
            title = "Steady progress"
        elif delta >= -10.0:
            title = "Working through it"
        else:
            title = "Tough game"

        parts: list[str] = []

        # Sentence 1 — rating-change context.
        if delta > 0.5:
            parts.append(f"Rating moved +{delta:.0f} this game.")
        elif delta < -0.5:
            parts.append(f"Rating moved {delta:.0f} this game.")
        else:
            parts.append("Rating held steady this game.")

        # Sentence 2 — engine-truth phase analysis from the
        # per-phase weakness rates (post-PR-#171 these are
        # phase-keyed: opening / middlegame / endgame).
        if weaknesses:
            dominant_phase = max(weaknesses, key=lambda k: weaknesses[k])
            rate = float(weaknesses[dominant_phase])
            if rate >= 0.5:
                parts.append(
                    f"Most mistakes were in the {dominant_phase}."
                )
            elif rate > 0.0:
                parts.append(
                    f"Some inaccuracy in the {dominant_phase}."
                )
            else:
                parts.append("No significant mistakes flagged.")
        else:
            parts.append("No significant mistakes flagged.")

        # Sentence 3 — pattern-aware closing.
        parts.append("Patterns sharpen with more games.")

        return CoachContent(
            title=title,
            description=" ".join(parts),
            payload={
                "learning_delta": delta,
                "weaknesses": weaknesses,
            },
        )
