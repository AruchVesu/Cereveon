"""
Unit tests for the mistake-pattern analytics layer.

Modules under test
------------------
llm.seca.analytics.mistake_stats
    MistakeCategory constants, _derive_category_scores, aggregate_from_weakness_dicts
llm.seca.analytics.training_recommendations
    _priority_from_ratio, generate_training_recommendations
llm.seca.analytics.events
    EventType.MISTAKE_PATTERN_RECORDED exists

Invariants pinned
-----------------
1. CATEGORY_CONSTANTS: All four MistakeCategory strings are non-empty and unique.
2. PHASE_MAPPING: Each known phase maps to at least one category.
3. CATEGORY_SCORES: Scores are in [0, 1] for rates in [0, 1].
4. AGGREGATION_EMPTY: Empty list → zero-games MistakeStats with empty dicts.
5. AGGREGATION_SINGLE: Single game → phase_rates equal the input dict.
6. AGGREGATION_MULTIPLE: Multi-game rates are averaged per phase.
7. AGGREGATION_DOMINANT: dominant_category is the max-score category.
8. AGGREGATION_INVALID_VALUES: Negative and non-numeric rates are skipped.
9. AGGREGATION_MISSING_PHASE: Missing phase in a game contributes 0 to average.
10. DETERMINISM: Identical inputs produce identical outputs (called twice).
11. NO_RECOMMENDATION_BELOW_THRESHOLD: Score below threshold → no focus item.
12. RECOMMENDATION_AT_THRESHOLD: Score exactly at threshold → low-priority item.
13. RECOMMENDATION_PRIORITY_HIGH: Score >= 2× threshold → "high" priority.
14. RECOMMENDATION_PRIORITY_MEDIUM: Score >= 1.25× threshold → "medium".
15. RECOMMENDATION_ORDER: Results are sorted high → medium → low.
16. RECOMMENDATION_EMPTY_STATS: Zero-games stats → empty recommendation list.
17. RECOMMENDATION_DETERMINISM: Same stats → same ordered result list.
18. EVENT_TYPE_EXISTS: EventType.MISTAKE_PATTERN_RECORDED is a non-empty string.
19. LAYER_BOUNDARY: mistake_stats.py does not import brain/coach/skills/engine modules.
20. LAYER_BOUNDARY: training_recommendations.py does not import brain/coach/skills/engine modules.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from llm.seca.analytics.mistake_stats import (
    MistakeCategory,
    MistakeStats,
    _derive_category_scores,
    aggregate_from_weakness_dicts,
)
from llm.seca.analytics.training_recommendations import (
    TrainingFocus,
    _CATEGORY_RULES,
    _priority_from_ratio,
    generate_training_recommendations,
)
from llm.seca.analytics.events import EventType

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_imports(module_path: Path) -> set[str]:
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


# ---------------------------------------------------------------------------
# 1. MistakeCategory constants
# ---------------------------------------------------------------------------


class TestMistakeCategory:

    def test_all_categories_are_non_empty_strings(self):
        for cat in MistakeCategory.ALL:
            assert isinstance(cat, str) and cat, f"Category is empty: {cat!r}"

    def test_all_categories_are_unique(self):
        assert len(MistakeCategory.ALL) == len(
            set(MistakeCategory.ALL)
        ), "Duplicate category constant detected"

    def test_expected_four_categories(self):
        assert len(MistakeCategory.ALL) == 4

    def test_category_names_match_constants(self):
        assert MistakeCategory.OPENING_PREPARATION == "opening_preparation"
        assert MistakeCategory.TACTICAL_VISION == "tactical_vision"
        assert MistakeCategory.POSITIONAL_PLAY == "positional_play"
        assert MistakeCategory.ENDGAME_TECHNIQUE == "endgame_technique"


# ---------------------------------------------------------------------------
# 2. _derive_category_scores
# ---------------------------------------------------------------------------


class TestDeriveCategoryScores:

    def test_opening_maps_to_opening_preparation(self):
        scores = _derive_category_scores({"opening": 0.10})
        assert scores.get(MistakeCategory.OPENING_PREPARATION, 0) == pytest.approx(0.10)

    def test_endgame_maps_to_endgame_technique(self):
        scores = _derive_category_scores({"endgame": 0.08})
        assert scores.get(MistakeCategory.ENDGAME_TECHNIQUE, 0) == pytest.approx(0.08)

    def test_middlegame_maps_to_positional_and_tactical(self):
        scores = _derive_category_scores({"middlegame": 0.10})
        pos = scores.get(MistakeCategory.POSITIONAL_PLAY, 0)
        tac = scores.get(MistakeCategory.TACTICAL_VISION, 0)
        assert pos == pytest.approx(0.06)  # 0.10 × 0.6
        assert tac == pytest.approx(0.04)  # 0.10 × 0.4

    def test_middlegame_positional_score_exceeds_tactical(self):
        scores = _derive_category_scores({"middlegame": 0.20})
        assert scores[MistakeCategory.POSITIONAL_PLAY] > scores[MistakeCategory.TACTICAL_VISION]

    def test_scores_are_bounded_for_rate_one(self):
        scores = _derive_category_scores({"opening": 1.0, "middlegame": 1.0, "endgame": 1.0})
        for cat, score in scores.items():
            assert 0.0 <= score <= 1.0, f"Score out of [0,1] for {cat}: {score}"

    def test_unknown_phase_is_ignored(self):
        scores = _derive_category_scores({"unknown_phase": 0.5})
        assert scores == {}

    def test_empty_phase_rates_returns_empty_scores(self):
        assert _derive_category_scores({}) == {}

    def test_multiple_phases_combine_independently(self):
        scores = _derive_category_scores({"opening": 0.10, "endgame": 0.08})
        assert scores.get(MistakeCategory.OPENING_PREPARATION) == pytest.approx(0.10)
        assert scores.get(MistakeCategory.ENDGAME_TECHNIQUE) == pytest.approx(0.08)
        # middlegame categories must be absent
        assert MistakeCategory.POSITIONAL_PLAY not in scores
        assert MistakeCategory.TACTICAL_VISION not in scores


# ---------------------------------------------------------------------------
# 3. aggregate_from_weakness_dicts
# ---------------------------------------------------------------------------


class TestAggregateFromWeaknessDicts:

    def test_empty_list_returns_zero_games(self):
        stats = aggregate_from_weakness_dicts([])
        assert stats.games_analyzed == 0
        assert stats.phase_rates == {}
        assert stats.category_scores == {}
        assert stats.dominant_category is None

    def test_single_game_phase_rates_match_input(self):
        wd = {"opening": 0.12, "middlegame": 0.05}
        stats = aggregate_from_weakness_dicts([wd])
        assert stats.games_analyzed == 1
        assert stats.phase_rates["opening"] == pytest.approx(0.12)
        assert stats.phase_rates["middlegame"] == pytest.approx(0.05)
        assert "endgame" not in stats.phase_rates

    def test_multiple_games_averaged_per_phase(self):
        dicts = [
            {"opening": 0.10, "endgame": 0.06},
            {"opening": 0.20, "endgame": 0.02},
        ]
        stats = aggregate_from_weakness_dicts(dicts)
        assert stats.games_analyzed == 2
        assert stats.phase_rates["opening"] == pytest.approx(0.15)
        assert stats.phase_rates["endgame"] == pytest.approx(0.04)

    def test_missing_phase_in_some_games_dilutes_average(self):
        """
        If 'endgame' appears in only 1 of 2 games, its average is rate/2
        (the missing game contributes 0 implicitly because the total is divided
        by the number of games, not the number of appearances).
        """
        dicts = [
            {"endgame": 0.10},
            {},  # no endgame key in this game
        ]
        stats = aggregate_from_weakness_dicts(dicts)
        # 0.10 / 2 games = 0.05
        assert stats.phase_rates["endgame"] == pytest.approx(0.05)

    def test_negative_rates_are_skipped(self):
        stats = aggregate_from_weakness_dicts([{"opening": -0.5}])
        assert "opening" not in stats.phase_rates

    def test_non_numeric_rates_are_skipped(self):
        stats = aggregate_from_weakness_dicts([{"opening": "bad"}])
        assert "opening" not in stats.phase_rates

    def test_dominant_category_is_max_score(self):
        # High opening rate → OPENING_PREPARATION dominates
        stats = aggregate_from_weakness_dicts([{"opening": 0.25, "endgame": 0.01}])
        assert stats.dominant_category == MistakeCategory.OPENING_PREPARATION

    def test_dominant_category_endgame(self):
        stats = aggregate_from_weakness_dicts([{"endgame": 0.20, "opening": 0.01}])
        assert stats.dominant_category == MistakeCategory.ENDGAME_TECHNIQUE

    def test_dominant_category_none_for_empty(self):
        stats = aggregate_from_weakness_dicts([])
        assert stats.dominant_category is None

    def test_games_analyzed_counts_all_dicts_including_empty(self):
        stats = aggregate_from_weakness_dicts([{}, {}, {"opening": 0.1}])
        assert stats.games_analyzed == 3

    def test_all_phase_rates_non_negative(self):
        dicts = [{"opening": 0.1}, {"middlegame": 0.2}, {"endgame": 0.05}]
        stats = aggregate_from_weakness_dicts(dicts)
        for phase, rate in stats.phase_rates.items():
            assert rate >= 0.0, f"Negative rate for phase {phase}: {rate}"

    def test_category_scores_present_for_known_phases(self):
        stats = aggregate_from_weakness_dicts([{"opening": 0.1, "endgame": 0.1}])
        assert MistakeCategory.OPENING_PREPARATION in stats.category_scores
        assert MistakeCategory.ENDGAME_TECHNIQUE in stats.category_scores

    def test_determinism_same_input_same_output(self):
        dicts = [{"opening": 0.15, "middlegame": 0.08, "endgame": 0.04}]
        s1 = aggregate_from_weakness_dicts(dicts)
        s2 = aggregate_from_weakness_dicts(dicts)
        assert s1.games_analyzed == s2.games_analyzed
        assert s1.phase_rates == s2.phase_rates
        assert s1.category_scores == s2.category_scores
        assert s1.dominant_category == s2.dominant_category

    def test_large_number_of_games_correct_average(self):
        n = 100
        rate = 0.10
        dicts = [{"middlegame": rate}] * n
        stats = aggregate_from_weakness_dicts(dicts)
        assert stats.games_analyzed == n
        assert stats.phase_rates["middlegame"] == pytest.approx(rate)


# ---------------------------------------------------------------------------
# 4. _priority_from_ratio
# ---------------------------------------------------------------------------


class TestPriorityFromRatio:

    def test_ratio_exactly_one_is_low(self):
        assert _priority_from_ratio(1.0) == "low"

    def test_ratio_below_125_is_low(self):
        assert _priority_from_ratio(1.1) == "low"

    def test_ratio_exactly_125_is_medium(self):
        assert _priority_from_ratio(1.25) == "medium"

    def test_ratio_between_125_and_200_is_medium(self):
        assert _priority_from_ratio(1.5) == "medium"
        assert _priority_from_ratio(1.99) == "medium"

    def test_ratio_exactly_200_is_high(self):
        assert _priority_from_ratio(2.0) == "high"

    def test_ratio_above_200_is_high(self):
        assert _priority_from_ratio(3.0) == "high"
        assert _priority_from_ratio(10.0) == "high"


# ---------------------------------------------------------------------------
# 5. generate_training_recommendations
# ---------------------------------------------------------------------------


class TestGenerateTrainingRecommendations:

    def _stats_with_scores(self, scores: dict[str, float]) -> MistakeStats:
        dominant = max(scores, key=lambda c: scores[c]) if scores else None
        return MistakeStats(
            games_analyzed=5,
            phase_rates={},
            category_scores=scores,
            dominant_category=dominant,
        )

    def test_empty_stats_returns_empty_list(self):
        stats = MistakeStats(games_analyzed=0)
        assert generate_training_recommendations(stats) == []

    def test_all_scores_below_threshold_returns_empty(self):
        scores = {cat: 0.001 for cat in MistakeCategory.ALL}
        stats = self._stats_with_scores(scores)
        assert generate_training_recommendations(stats) == []

    def test_score_exactly_at_threshold_returns_low_priority(self):
        for category, (threshold, _) in _CATEGORY_RULES.items():
            scores = {cat: 0.0 for cat in MistakeCategory.ALL}
            scores[category] = threshold
            stats = self._stats_with_scores(scores)
            recs = generate_training_recommendations(stats)
            assert len(recs) == 1
            assert recs[0].category == category
            assert (
                recs[0].priority == "low"
            ), f"Score exactly at threshold should be 'low', got {recs[0].priority!r}"

    def test_score_at_twice_threshold_returns_high_priority(self):
        for category, (threshold, _) in _CATEGORY_RULES.items():
            scores = {cat: 0.0 for cat in MistakeCategory.ALL}
            scores[category] = threshold * 2.0
            stats = self._stats_with_scores(scores)
            recs = generate_training_recommendations(stats)
            assert (
                recs[0].priority == "high"
            ), f"Score at 2× threshold should be 'high' for {category}"

    def test_score_at_125x_threshold_returns_medium(self):
        for category, (threshold, _) in _CATEGORY_RULES.items():
            scores = {cat: 0.0 for cat in MistakeCategory.ALL}
            scores[category] = threshold * 1.25
            stats = self._stats_with_scores(scores)
            recs = generate_training_recommendations(stats)
            assert (
                recs[0].priority == "medium"
            ), f"Score at 1.25× threshold should be 'medium' for {category}"

    def test_results_sorted_high_before_medium_before_low(self):
        t_opening = _CATEGORY_RULES[MistakeCategory.OPENING_PREPARATION][0]
        t_tactical = _CATEGORY_RULES[MistakeCategory.TACTICAL_VISION][0]
        t_endgame = _CATEGORY_RULES[MistakeCategory.ENDGAME_TECHNIQUE][0]
        scores = {
            MistakeCategory.OPENING_PREPARATION: t_opening * 1.1,  # low
            MistakeCategory.TACTICAL_VISION: t_tactical * 2.5,  # high
            MistakeCategory.POSITIONAL_PLAY: 0.0,
            MistakeCategory.ENDGAME_TECHNIQUE: t_endgame * 1.3,  # medium
        }
        stats = self._stats_with_scores(scores)
        recs = generate_training_recommendations(stats)

        priorities = [r.priority for r in recs]
        assert priorities == sorted(
            priorities, key=lambda p: {"high": 0, "medium": 1, "low": 2}[p]
        ), f"Recommendations not sorted by priority: {priorities}"
        assert recs[0].priority == "high"

    def test_each_focus_has_non_empty_rationale(self):
        scores = {cat: threshold * 2.0 for cat, (threshold, _) in _CATEGORY_RULES.items()}
        stats = self._stats_with_scores(scores)
        recs = generate_training_recommendations(stats)
        for rec in recs:
            assert rec.rationale, f"Empty rationale for category {rec.category!r}"

    def test_each_focus_category_is_known(self):
        scores = {cat: threshold * 2.0 for cat, (threshold, _) in _CATEGORY_RULES.items()}
        stats = self._stats_with_scores(scores)
        recs = generate_training_recommendations(stats)
        for rec in recs:
            assert (
                rec.category in MistakeCategory.ALL
            ), f"Unknown category in recommendation: {rec.category!r}"

    def test_focus_is_frozen_dataclass(self):
        f = TrainingFocus(category="x", priority="low", rationale="r")
        with pytest.raises((AttributeError, TypeError)):
            f.category = "modified"  # type: ignore[misc]

    def test_determinism_same_stats_same_order(self):
        scores = {
            MistakeCategory.OPENING_PREPARATION: 0.15,
            MistakeCategory.TACTICAL_VISION: 0.10,
            MistakeCategory.POSITIONAL_PLAY: 0.07,
            MistakeCategory.ENDGAME_TECHNIQUE: 0.09,
        }
        stats = self._stats_with_scores(scores)
        recs1 = generate_training_recommendations(stats)
        recs2 = generate_training_recommendations(stats)
        assert [(r.category, r.priority) for r in recs1] == [
            (r.category, r.priority) for r in recs2
        ]

    def test_real_aggregation_pipeline_produces_recommendations(self):
        """
        End-to-end test: raw weakness dicts → MistakeStats → recommendations.
        A player with chronic opening errors must get an opening focus item.
        """
        opening_threshold = _CATEGORY_RULES[MistakeCategory.OPENING_PREPARATION][0]
        high_opening_rate = opening_threshold * 3.0
        dicts = [{"opening": high_opening_rate}] * 10
        stats = aggregate_from_weakness_dicts(dicts)
        recs = generate_training_recommendations(stats)

        categories = [r.category for r in recs]
        assert (
            MistakeCategory.OPENING_PREPARATION in categories
        ), f"Expected OPENING_PREPARATION in recommendations, got {categories}"
        opening_recs = [r for r in recs if r.category == MistakeCategory.OPENING_PREPARATION]
        assert opening_recs[0].priority == "high"

    def test_endgame_only_player_gets_endgame_recommendation(self):
        endgame_threshold = _CATEGORY_RULES[MistakeCategory.ENDGAME_TECHNIQUE][0]
        dicts = [{"endgame": endgame_threshold * 2.5}] * 5
        stats = aggregate_from_weakness_dicts(dicts)
        recs = generate_training_recommendations(stats)
        assert any(r.category == MistakeCategory.ENDGAME_TECHNIQUE for r in recs)

    def test_clean_player_gets_no_recommendations(self):
        """A player with near-zero mistake rates should get no focus items."""
        dicts = [{"opening": 0.001, "middlegame": 0.001, "endgame": 0.001}] * 10
        stats = aggregate_from_weakness_dicts(dicts)
        recs = generate_training_recommendations(stats)
        assert recs == []


# ---------------------------------------------------------------------------
# 6. EventType.MISTAKE_PATTERN_RECORDED
# ---------------------------------------------------------------------------


class TestMistakePatternEventType:

    def test_event_type_exists(self):
        assert hasattr(EventType, "MISTAKE_PATTERN_RECORDED")

    def test_event_type_is_non_empty_string(self):
        value = EventType.MISTAKE_PATTERN_RECORDED
        assert isinstance(value, str) and value

    def test_event_type_value(self):
        assert EventType.MISTAKE_PATTERN_RECORDED == "mistake_pattern_recorded"

    def test_event_type_is_distinct_from_game_finished(self):
        assert EventType.MISTAKE_PATTERN_RECORDED != EventType.GAME_FINISHED


# ---------------------------------------------------------------------------
# 7. Layer boundary: new analytics modules must not import brain/coach/engine
# ---------------------------------------------------------------------------


class TestAnalyticsNewModulesLayerBoundary:
    """
    mistake_stats.py and training_recommendations.py must stay within the
    analytics layer — no imports from brain, coach, skills, or engine modules.
    """

    _FORBIDDEN_PATTERNS = ("brain", "coach", "skills", "engine_eval", "engines")

    def _check_file(self, rel_path: str) -> set[str]:
        path = PROJECT_ROOT / rel_path
        assert path.exists(), f"{rel_path} not found"
        imports = _get_imports(path)
        return {
            imp for imp in imports if any(pattern in imp for pattern in self._FORBIDDEN_PATTERNS)
        }

    def test_mistake_stats_no_brain_imports(self):
        violations = self._check_file("llm/seca/analytics/mistake_stats.py")
        assert not violations, f"mistake_stats.py imports forbidden modules: {violations}"

    def test_training_recommendations_no_brain_imports(self):
        violations = self._check_file("llm/seca/analytics/training_recommendations.py")
        assert (
            not violations
        ), f"training_recommendations.py imports forbidden modules: {violations}"

    def test_mistake_stats_no_sqlalchemy_imports(self):
        """Pure-Python analytics helpers must not import SQLAlchemy."""
        path = PROJECT_ROOT / "llm/seca/analytics/mistake_stats.py"
        imports = _get_imports(path)
        sql_imports = {i for i in imports if "sqlalchemy" in i}
        assert not sql_imports, (
            f"mistake_stats.py imports SQLAlchemy: {sql_imports}. "
            "Aggregation logic must be pure Python."
        )

    def test_training_recommendations_no_sqlalchemy_imports(self):
        """Recommendation engine must not import SQLAlchemy."""
        path = PROJECT_ROOT / "llm/seca/analytics/training_recommendations.py"
        imports = _get_imports(path)
        sql_imports = {i for i in imports if "sqlalchemy" in i}
        assert not sql_imports, (
            f"training_recommendations.py imports SQLAlchemy: {sql_imports}. "
            "Recommendation logic must be pure Python."
        )
