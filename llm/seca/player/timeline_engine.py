from datetime import datetime
from typing import List

from llm.seca.player.timeline_types import GameRecord, SkillTimeline
from llm.seca.player.skill_timeline import build_skill_points
from llm.seca.player.rating_curve import apply_rating_curve


class PlayerSkillTimelineEngine:
    """
    Builds longitudinal learning curve from analyzed games.
    """

    def __init__(self):
        self.games: List[GameRecord] = []

    # -----------------------------
    # Add analyzed game
    # -----------------------------
    def add_game(
        self,
        *,
        confidence: float,
        acpl: float,
        blunders: int,
        result: float,
        date: datetime | None = None,
    ):
        self.games.append(
            GameRecord(
                date=date or datetime.utcnow(),
                confidence=confidence,
                acpl=acpl,
                blunders=blunders,
                result=result,
            )
        )

    # -----------------------------
    # Build full timeline
    # -----------------------------
    def build(self) -> SkillTimeline:
        # sort chronologically
        games_sorted = sorted(self.games, key=lambda g: g.date)

        # skill points
        points = build_skill_points(games_sorted)

        # rating curve
        points = apply_rating_curve(points)

        return SkillTimeline(
            games=games_sorted,
            points=points,
        )

    # -----------------------------
    # Convenience metrics
    # -----------------------------
    def latest_rating(self) -> float | None:
        timeline = self.build()
        if not timeline.points:
            return None
        return timeline.points[-1].rating

    def improvement_rate(self) -> float:
        timeline = self.build()
        if len(timeline.points) < 2:
            return 0.0

        return timeline.points[-1].skill - timeline.points[0].skill
