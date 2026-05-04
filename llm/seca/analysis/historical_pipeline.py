"""
Historical game analysis pipeline.

Aggregates weakness vectors from stored GameEvent records (computed at
game-finish time by GameWeaknessAnalyzer) into MistakeStats and persists a
MISTAKE_PATTERN_RECORDED analytics event.

No live Stockfish process is required — weakness_json values are read directly
from DB records.  Engine evaluation is authoritative because it was performed
during live play; this pipeline merely aggregates the stored output.

Classes
-------
HistoricalAnalysisPipeline
    Accepts player_id + list[GameEvent], decodes weaknesses_json, aggregates
    via aggregate_from_weakness_dicts, persists analytics, returns MistakeStats.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from ..analytics.events import EventType
from ..analytics.logger import AnalyticsLogger
from ..analytics.mistake_stats import MistakeStats, aggregate_from_weakness_dicts
from ..events.models import GameEvent


class HistoricalAnalysisPipeline:
    """
    Aggregate stored GameEvent weakness vectors into a ``MistakeStats`` record
    and persist a ``MISTAKE_PATTERN_RECORDED`` analytics event.

    Parameters
    ----------
    db:
        Active SQLAlchemy session used for analytics persistence.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, player_id: str, game_events: list[GameEvent]) -> MistakeStats:
        """
        Analyse ``game_events`` for ``player_id`` and persist analytics.

        Parameters
        ----------
        player_id:
            Identifier for the player whose games are being analysed.
        game_events:
            Ordered list of GameEvent ORM records.  Records with a missing or
            malformed ``weaknesses_json`` field are skipped silently.

        Returns
        -------
        MistakeStats
            Aggregated mistake statistics.  ``games_analyzed`` equals the
            number of records whose ``weaknesses_json`` decoded to a valid dict.
        """
        weakness_dicts = self._extract_weakness_dicts(game_events)
        stats = aggregate_from_weakness_dicts(weakness_dicts)

        AnalyticsLogger(self._db).log(
            event_type=EventType.MISTAKE_PATTERN_RECORDED,
            player_id=player_id,
            payload={
                "games_analyzed": stats.games_analyzed,
                "phase_rates": stats.phase_rates,
                "category_scores": stats.category_scores,
                "dominant_category": stats.dominant_category,
            },
        )

        return stats

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_weakness_dicts(game_events: list[GameEvent]) -> list[dict]:
        """
        Decode ``weaknesses_json`` from each GameEvent record.

        Only successfully decoded, non-empty dicts are returned.
        Malformed or absent JSON is silently skipped.
        """
        result: list[dict] = []
        for event in game_events:
            raw = getattr(event, "weaknesses_json", None)
            if not raw:
                continue
            try:
                decoded = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if isinstance(decoded, dict):
                result.append(decoded)
        return result
