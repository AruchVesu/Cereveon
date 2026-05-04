"""
Tests for GET /player/progress — progress dashboard endpoint.

Pinned invariants
-----------------
1. CURRENT_HAS_ALL_REQUIRED_FIELDS
2. HISTORY_IS_A_LIST
3. ANALYSIS_HAS_REQUIRED_KEYS
4. SKILL_VECTOR_IS_DICT
5. TIER_IS_VALID_STRING
6. OPPONENT_ELO_IN_VALID_RANGE
7. EXPLANATION_DEPTH_IN_0_1
8. HISTORY_ITEM_HAS_WEAKNESSES
9. HISTORY_ITEM_HAS_RATING
10. ANALYSIS_CATEGORY_SCORES_MATCH_PIPELINE
11. RECOMMENDATIONS_HAVE_PRIORITY_AND_RATIONALE
12. WORLD_MODEL_MATCHES_COMPUTE_ADAPTATION
13. EMPTY_HISTORY_RETURNS_ZERO_GAMES_ANALYZED
14. SAFE_MODE_DOES_NOT_BLOCK_PROGRESS_ENDPOINT
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from llm.seca.analytics.router import get_player_progress


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKILL_VECTOR = {"tactics": 0.15, "endgame": 0.08, "opening": 0.12}


def _make_player(rating: float = 1500.0, confidence: float = 0.60) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        email="test@example.com",
        rating=rating,
        confidence=confidence,
        skill_vector_json=json.dumps(_SKILL_VECTOR),
    )


def _make_game_event(
    game_id: str = "g1",
    result: str = "win",
    accuracy: float = 0.78,
    weaknesses: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=game_id,
        result=result,
        accuracy=accuracy,
        weaknesses_json=json.dumps(weaknesses or {"opening": 0.10, "middlegame": 0.05, "endgame": 0.00}),
        created_at=datetime(2026, 3, 30, 12, 0, 0),
    )


def _make_db(events: list, rating_after: float | None = 1510.0, confidence_after: float | None = 0.62):
    db = MagicMock()

    # GameEvent query chain
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = events

    # RatingUpdate + ConfidenceUpdate: first() returns a stub
    ru = SimpleNamespace(rating_after=rating_after) if rating_after is not None else None
    cu = SimpleNamespace(confidence_after=confidence_after) if confidence_after is not None else None
    db.query.return_value.filter.return_value.first.return_value = ru

    # Make the second filter().first() return the confidence update (crude but
    # sufficient — both return the same stub values for testing purposes)
    return db, ru, cu


# ---------------------------------------------------------------------------
# 1. Current snapshot fields
# ---------------------------------------------------------------------------


class TestCurrentSnapshot:
    """CURRENT_HAS_ALL_REQUIRED_FIELDS through WORLD_MODEL_MATCHES_COMPUTE_ADAPTATION"""

    def _call(self, rating: float = 1500.0, confidence: float = 0.60):
        player = _make_player(rating=rating, confidence=confidence)
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        with patch("llm.seca.analytics.router.EventStorage") as MockStorage:
            MockStorage.return_value.get_recent_games.return_value = []
            return get_player_progress(player=player, db=db)

    def test_current_has_all_required_fields(self):
        """CURRENT_HAS_ALL_REQUIRED_FIELDS"""
        result = self._call()
        current = result["current"]
        required = {"rating", "confidence", "skill_vector", "tier", "teaching_style",
                    "opponent_elo", "explanation_depth", "concept_complexity"}
        missing = required - set(current.keys())
        assert not missing, f"current missing fields: {missing}"

    def test_skill_vector_is_dict(self):
        """SKILL_VECTOR_IS_DICT"""
        result = self._call()
        assert isinstance(result["current"]["skill_vector"], dict)

    def test_tier_is_valid_string(self):
        """TIER_IS_VALID_STRING"""
        valid = {"beginner", "intermediate", "advanced"}
        assert self._call(rating=800.0)["current"]["tier"] == "beginner"
        assert self._call(rating=1500.0)["current"]["tier"] == "intermediate"
        assert self._call(rating=2100.0)["current"]["tier"] == "advanced"

    def test_opponent_elo_in_valid_range(self):
        """OPPONENT_ELO_IN_VALID_RANGE — [600, 2400]"""
        for rating in [400.0, 1200.0, 2400.0]:
            elo = self._call(rating=rating)["current"]["opponent_elo"]
            assert 600 <= elo <= 2400, f"opponent_elo={elo} out of range for rating={rating}"

    def test_explanation_depth_in_0_1(self):
        """EXPLANATION_DEPTH_IN_0_1"""
        for rating in [400.0, 1200.0, 2400.0]:
            depth = self._call(rating=rating)["current"]["explanation_depth"]
            assert 0.0 <= depth <= 1.0, f"explanation_depth={depth} out of range"

    def test_world_model_matches_compute_adaptation(self):
        """WORLD_MODEL_MATCHES_COMPUTE_ADAPTATION — no divergence between endpoint and coupling"""
        from llm.seca.adaptation.coupling import compute_adaptation

        rating, confidence = 1500.0, 0.60
        adaptation = compute_adaptation(rating, confidence)
        result = self._call(rating=rating, confidence=confidence)
        current = result["current"]

        assert current["opponent_elo"] == adaptation["opponent"]["target_elo"]
        assert current["teaching_style"] == adaptation["teaching"]["style"]
        assert abs(current["explanation_depth"] - round(adaptation["profile"].explanation_depth, 3)) < 0.001


# ---------------------------------------------------------------------------
# 2. History items
# ---------------------------------------------------------------------------


class TestHistoryItems:
    """HISTORY_IS_A_LIST through HISTORY_ITEM_HAS_RATING"""

    def _call(self, events):
        player = _make_player()
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = events
        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
            rating_after=1510.0, confidence_after=0.62
        )
        with patch("llm.seca.analytics.router.EventStorage") as MockStorage:
            MockStorage.return_value.get_recent_games.return_value = []
            return get_player_progress(player=player, db=db)

    def test_history_is_a_list(self):
        """HISTORY_IS_A_LIST"""
        result = self._call([])
        assert isinstance(result["history"], list)

    def test_history_item_has_weaknesses(self):
        """HISTORY_ITEM_HAS_WEAKNESSES"""
        ev = _make_game_event(weaknesses={"opening": 0.15, "middlegame": 0.05, "endgame": 0.00})
        result = self._call([ev])
        item = result["history"][0]
        assert "weaknesses" in item
        assert isinstance(item["weaknesses"], dict)
        assert "opening" in item["weaknesses"]

    def test_history_item_has_rating(self):
        """HISTORY_ITEM_HAS_RATING"""
        ev = _make_game_event()
        result = self._call([ev])
        item = result["history"][0]
        assert "rating_after" in item

    def test_history_item_schema(self):
        """Each item has game_id, result, accuracy, created_at."""
        ev = _make_game_event(game_id="abc123", result="loss", accuracy=0.45)
        result = self._call([ev])
        item = result["history"][0]
        assert item["game_id"] == "abc123"
        assert item["result"] == "loss"
        assert abs(item["accuracy"] - 0.45) < 0.01
        assert item["created_at"] is not None

    def test_malformed_weaknesses_json_returns_empty_dict(self):
        """Malformed weaknesses_json must not crash — returns {}."""
        ev = _make_game_event()
        ev.weaknesses_json = "NOT_JSON{{{"
        result = self._call([ev])
        assert result["history"][0]["weaknesses"] == {}


# ---------------------------------------------------------------------------
# 3. Analysis fields
# ---------------------------------------------------------------------------


class TestAnalysisFields:
    """ANALYSIS_HAS_REQUIRED_KEYS through EMPTY_HISTORY_RETURNS_ZERO_GAMES_ANALYZED"""

    def test_analysis_has_required_keys(self):
        """ANALYSIS_HAS_REQUIRED_KEYS"""
        player = _make_player()
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        with patch("llm.seca.analytics.router.EventStorage") as MockStorage:
            MockStorage.return_value.get_recent_games.return_value = []
            result = get_player_progress(player=player, db=db)
        analysis = result["analysis"]
        required = {"dominant_category", "games_analyzed", "category_scores", "phase_rates", "recommendations"}
        missing = required - set(analysis.keys())
        assert not missing, f"analysis missing fields: {missing}"

    def test_empty_history_returns_zero_games_analyzed(self):
        """EMPTY_HISTORY_RETURNS_ZERO_GAMES_ANALYZED"""
        player = _make_player()
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        with patch("llm.seca.analytics.router.EventStorage") as MockStorage:
            MockStorage.return_value.get_recent_games.return_value = []
            result = get_player_progress(player=player, db=db)
        assert result["analysis"]["games_analyzed"] == 0
        assert result["analysis"]["dominant_category"] is None
        assert result["analysis"]["recommendations"] == []

    def test_recommendations_have_priority_and_rationale(self):
        """RECOMMENDATIONS_HAVE_PRIORITY_AND_RATIONALE"""
        from llm.seca.analytics.mistake_stats import MistakeStats
        from llm.seca.analytics.training_recommendations import generate_training_recommendations

        stats = MistakeStats(
            games_analyzed=5,
            phase_rates={"opening": 0.20, "middlegame": 0.10, "endgame": 0.08},
            category_scores={
                "opening_preparation": 0.20,
                "tactical_vision": 0.04,
                "positional_play": 0.06,
                "endgame_technique": 0.08,
            },
            dominant_category="opening_preparation",
        )
        recommendations = generate_training_recommendations(stats)
        for rec in recommendations:
            assert rec.priority in {"high", "medium", "low"}, f"bad priority: {rec.priority}"
            assert isinstance(rec.rationale, str) and len(rec.rationale) > 0

    def test_analysis_category_scores_match_pipeline(self):
        """ANALYSIS_CATEGORY_SCORES_MATCH_PIPELINE — endpoint delegates correctly."""
        from llm.seca.analytics.mistake_stats import MistakeStats

        fake_stats = MistakeStats(
            games_analyzed=3,
            phase_rates={"opening": 0.10, "middlegame": 0.05, "endgame": 0.02},
            category_scores={
                "opening_preparation": 0.10,
                "tactical_vision": 0.02,
                "positional_play": 0.03,
                "endgame_technique": 0.02,
            },
            dominant_category="opening_preparation",
        )
        player = _make_player()
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        with (
            patch("llm.seca.analytics.router.EventStorage") as MockStorage,
            patch("llm.seca.analytics.router.HistoricalAnalysisPipeline") as MockPipeline,
        ):
            MockStorage.return_value.get_recent_games.return_value = [object()]
            MockPipeline.return_value.run.return_value = fake_stats

            result = get_player_progress(player=player, db=db)

        analysis = result["analysis"]
        assert analysis["games_analyzed"] == 3
        assert analysis["dominant_category"] == "opening_preparation"
        assert abs(analysis["category_scores"]["opening_preparation"] - 0.10) < 0.001


# ---------------------------------------------------------------------------
# 4. SAFE_MODE transparency
# ---------------------------------------------------------------------------


def test_safe_mode_does_not_block_progress_endpoint():
    """SAFE_MODE_DOES_NOT_BLOCK_PROGRESS_ENDPOINT — /player/progress is read-only."""
    from llm.seca.runtime.safe_mode import SAFE_MODE

    # The endpoint must work regardless of SAFE_MODE (it is read-only, no RL).
    player = _make_player()
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    with patch("llm.seca.analytics.router.EventStorage") as MockStorage:
        MockStorage.return_value.get_recent_games.return_value = []
        result = get_player_progress(player=player, db=db)
    assert "current" in result
    assert "history" in result
    assert "analysis" in result
