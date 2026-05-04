"""
Unit tests for the historical game analysis pipeline.

Modules under test
------------------
llm.seca.analysis.historical_pipeline
    HistoricalAnalysisPipeline._extract_weakness_dicts, .run
llm.seca.analysis.pgn_loader
    load_moves_from_pgn
llm.seca.analysis.mistake_classifier
    classify_delta boundary values
llm.seca.events.storage
    EventStorage.get_recent_games (player-scoped)
    EventStorage.get_all_recent_games (global, renamed from duplicate)

Invariants pinned
-----------------
 1. PGN_MOVES_SCHOLARS_MATE:   Scholar's Mate PGN yields 7 SAN moves in order.
 2. PGN_MOVES_EMPTY_FILE:      PGN file with no game returns empty list.
 3. PGN_MOVES_SINGLE:          One-move PGN returns a list of length 1.
 4. PGN_MOVES_NOT_FOUND:       Missing file raises FileNotFoundError.
 5. PGN_MOVES_WHITESPACE_FILE: File containing only whitespace returns [].
 6. CLASSIFY_DELTA_OK:         |delta| < 50 → "ok".
 7. CLASSIFY_DELTA_OK_BOUNDARY: |delta| == 49 → "ok".
 8. CLASSIFY_DELTA_INACCURACY: |delta| == 50 → "inaccuracy".
 9. CLASSIFY_DELTA_INACCURACY_BOUNDARY: |delta| == 149 → "inaccuracy".
10. CLASSIFY_DELTA_MISTAKE:    |delta| == 150 → "mistake".
11. CLASSIFY_DELTA_MISTAKE_BOUNDARY: |delta| == 299 → "mistake".
12. CLASSIFY_DELTA_BLUNDER:    |delta| == 300 → "blunder".
13. CLASSIFY_DELTA_NEGATIVE:   Negative delta uses abs value (sign-agnostic).
14. EXTRACT_EMPTY_EVENTS:      No game events → empty list.
15. EXTRACT_VALID_JSON:        Valid weaknesses_json dict is decoded and returned.
16. EXTRACT_NULL_JSON:         None weaknesses_json is skipped.
17. EXTRACT_BLANK_JSON:        Empty-string weaknesses_json is skipped.
18. EXTRACT_MALFORMED_JSON:    Unparseable JSON is skipped.
19. EXTRACT_NON_DICT_JSON:     JSON array is skipped (must be dict).
20. EXTRACT_MULTIPLE:          Multiple valid events → all dicts returned.
21. PIPELINE_ZERO_EVENTS:      run() with no events → MistakeStats(games_analyzed=0).
22. PIPELINE_SINGLE_EVENT:     run() with one event → correct aggregation.
23. PIPELINE_LOGS_EVENT_TYPE:  run() calls AnalyticsLogger.log with MISTAKE_PATTERN_RECORDED.
24. PIPELINE_LOGS_PLAYER_ID:   run() passes player_id to AnalyticsLogger.
25. PIPELINE_PAYLOAD_COMPLETE: Logged payload contains all four expected keys.
26. STORAGE_PLAYER_SCOPED:     get_recent_games filters by player_id.
27. STORAGE_GLOBAL:            get_all_recent_games has no player_id filter.
28. STORAGE_METHODS_DISTINCT:  Both methods exist and are distinct callables.
29. LAYER_BOUNDARY:            historical_pipeline does not import brain/coach/RL.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from llm.seca.analysis.historical_pipeline import HistoricalAnalysisPipeline
from llm.seca.analysis.mistake_classifier import classify_delta
from llm.seca.analysis.pgn_loader import load_moves_from_pgn
from llm.seca.analytics.events import EventType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHOLARS_MATE_PGN = """\
[Event "Test"]
[Site "?"]
[Date "????.??.??"]
[Round "?"]
[White "?"]
[Black "?"]
[Result "1-0"]

1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0
"""

_ONE_MOVE_PGN = """\
[Event "Test"]
[Result "*"]

1. e4 *
"""


def _make_event(weaknesses_json: str | None) -> SimpleNamespace:
    """Build a minimal GameEvent-like object without hitting the ORM."""
    return SimpleNamespace(weaknesses_json=weaknesses_json)


# ---------------------------------------------------------------------------
# 1–5  load_moves_from_pgn
# ---------------------------------------------------------------------------


class TestLoadMovesFromPgn:

    def test_scholars_mate_yields_seven_moves(self, tmp_path):
        """PGN_MOVES_SCHOLARS_MATE: Scholar's Mate PGN yields 7 SAN moves."""
        pgn_file = tmp_path / "scholars.pgn"
        pgn_file.write_text(_SCHOLARS_MATE_PGN, encoding="utf-8")
        moves = load_moves_from_pgn(str(pgn_file))
        assert len(moves) == 7
        assert moves[0] == "e4"
        assert moves[-1] == "Qxf7#"

    def test_empty_pgn_file_returns_empty_list(self, tmp_path):
        """PGN_MOVES_EMPTY_FILE: PGN file with no game returns empty list."""
        pgn_file = tmp_path / "empty.pgn"
        pgn_file.write_text("", encoding="utf-8")
        assert load_moves_from_pgn(str(pgn_file)) == []

    def test_single_move_pgn_returns_one_move(self, tmp_path):
        """PGN_MOVES_SINGLE: One-move PGN returns a list of length 1."""
        pgn_file = tmp_path / "one.pgn"
        pgn_file.write_text(_ONE_MOVE_PGN, encoding="utf-8")
        moves = load_moves_from_pgn(str(pgn_file))
        assert len(moves) == 1
        assert moves[0] == "e4"

    def test_missing_file_raises_file_not_found(self, tmp_path):
        """PGN_MOVES_NOT_FOUND: Missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_moves_from_pgn(str(tmp_path / "nonexistent.pgn"))

    def test_whitespace_only_file_returns_empty_list(self, tmp_path):
        """PGN_MOVES_WHITESPACE_FILE: File containing only whitespace returns []."""
        pgn_file = tmp_path / "ws.pgn"
        pgn_file.write_text("   \n   \n", encoding="utf-8")
        assert load_moves_from_pgn(str(pgn_file)) == []


# ---------------------------------------------------------------------------
# 6–13  classify_delta boundary values
# ---------------------------------------------------------------------------


class TestClassifyDelta:

    def test_below_50_is_ok(self):
        """CLASSIFY_DELTA_OK: |delta| < 50 → 'ok'."""
        assert classify_delta(0.0) == "ok"
        assert classify_delta(10.0) == "ok"

    def test_49_is_ok(self):
        """CLASSIFY_DELTA_OK_BOUNDARY: |delta| == 49 → 'ok'."""
        assert classify_delta(49.0) == "ok"

    def test_50_is_inaccuracy(self):
        """CLASSIFY_DELTA_INACCURACY: |delta| == 50 → 'inaccuracy'."""
        assert classify_delta(50.0) == "inaccuracy"

    def test_149_is_inaccuracy(self):
        """CLASSIFY_DELTA_INACCURACY_BOUNDARY: |delta| == 149 → 'inaccuracy'."""
        assert classify_delta(149.0) == "inaccuracy"

    def test_150_is_mistake(self):
        """CLASSIFY_DELTA_MISTAKE: |delta| == 150 → 'mistake'."""
        assert classify_delta(150.0) == "mistake"

    def test_299_is_mistake(self):
        """CLASSIFY_DELTA_MISTAKE_BOUNDARY: |delta| == 299 → 'mistake'."""
        assert classify_delta(299.0) == "mistake"

    def test_300_is_blunder(self):
        """CLASSIFY_DELTA_BLUNDER: |delta| == 300 → 'blunder'."""
        assert classify_delta(300.0) == "blunder"

    def test_negative_delta_uses_abs(self):
        """CLASSIFY_DELTA_NEGATIVE: Negative delta uses abs value."""
        assert classify_delta(-300.0) == "blunder"
        assert classify_delta(-49.0) == "ok"
        assert classify_delta(-150.0) == "mistake"


# ---------------------------------------------------------------------------
# 14–20  _extract_weakness_dicts
# ---------------------------------------------------------------------------


class TestExtractWeaknessDicts:

    def test_no_events_returns_empty_list(self):
        """EXTRACT_EMPTY_EVENTS: No game events → empty list."""
        result = HistoricalAnalysisPipeline._extract_weakness_dicts([])
        assert result == []

    def test_valid_json_dict_is_decoded(self):
        """EXTRACT_VALID_JSON: Valid weaknesses_json dict is decoded and returned."""
        event = _make_event('{"opening": 0.12, "middlegame": 0.05}')
        result = HistoricalAnalysisPipeline._extract_weakness_dicts([event])
        assert result == [{"opening": 0.12, "middlegame": 0.05}]

    def test_none_weaknesses_json_is_skipped(self):
        """EXTRACT_NULL_JSON: None weaknesses_json is skipped."""
        event = _make_event(None)
        assert HistoricalAnalysisPipeline._extract_weakness_dicts([event]) == []

    def test_empty_string_weaknesses_json_is_skipped(self):
        """EXTRACT_BLANK_JSON: Empty-string weaknesses_json is skipped."""
        event = _make_event("")
        assert HistoricalAnalysisPipeline._extract_weakness_dicts([event]) == []

    def test_malformed_json_is_skipped(self):
        """EXTRACT_MALFORMED_JSON: Unparseable JSON is skipped."""
        event = _make_event("{not valid json}")
        assert HistoricalAnalysisPipeline._extract_weakness_dicts([event]) == []

    def test_json_array_is_skipped(self):
        """EXTRACT_NON_DICT_JSON: JSON array is skipped (must be dict)."""
        event = _make_event("[0.1, 0.2]")
        assert HistoricalAnalysisPipeline._extract_weakness_dicts([event]) == []

    def test_multiple_events_all_valid(self):
        """EXTRACT_MULTIPLE: Multiple valid events → all dicts returned."""
        events = [
            _make_event('{"opening": 0.1}'),
            _make_event('{"middlegame": 0.2}'),
            _make_event('{"endgame": 0.05}'),
        ]
        result = HistoricalAnalysisPipeline._extract_weakness_dicts(events)
        assert len(result) == 3
        assert result[0] == {"opening": 0.1}
        assert result[2] == {"endgame": 0.05}


# ---------------------------------------------------------------------------
# 21–25  HistoricalAnalysisPipeline.run
# ---------------------------------------------------------------------------


class TestHistoricalPipelineRun:

    def _make_pipeline(self):
        return HistoricalAnalysisPipeline(db=MagicMock())

    @patch("llm.seca.analysis.historical_pipeline.AnalyticsLogger")
    def test_zero_events_returns_zero_games_stats(self, MockLogger):
        """PIPELINE_ZERO_EVENTS: run() with no events → MistakeStats(games_analyzed=0)."""
        MockLogger.return_value = MagicMock()
        pipeline = self._make_pipeline()
        stats = pipeline.run("player1", [])
        assert stats.games_analyzed == 0
        assert stats.phase_rates == {}
        assert stats.category_scores == {}
        assert stats.dominant_category is None

    @patch("llm.seca.analysis.historical_pipeline.AnalyticsLogger")
    def test_single_event_aggregates_correctly(self, MockLogger):
        """PIPELINE_SINGLE_EVENT: run() with one valid event → correct aggregation."""
        MockLogger.return_value = MagicMock()
        pipeline = self._make_pipeline()
        events = [_make_event('{"opening": 0.20, "middlegame": 0.10}')]
        stats = pipeline.run("player1", events)
        assert stats.games_analyzed == 1
        assert abs(stats.phase_rates["opening"] - 0.20) < 1e-9
        assert abs(stats.phase_rates["middlegame"] - 0.10) < 1e-9

    @patch("llm.seca.analysis.historical_pipeline.AnalyticsLogger")
    def test_run_logs_mistake_pattern_event_type(self, MockLogger):
        """PIPELINE_LOGS_EVENT_TYPE: run() logs MISTAKE_PATTERN_RECORDED."""
        mock_logger_instance = MagicMock()
        MockLogger.return_value = mock_logger_instance
        pipeline = self._make_pipeline()
        pipeline.run("player1", [])
        mock_logger_instance.log.assert_called_once()
        kwargs = mock_logger_instance.log.call_args.kwargs
        assert kwargs["event_type"] == EventType.MISTAKE_PATTERN_RECORDED

    @patch("llm.seca.analysis.historical_pipeline.AnalyticsLogger")
    def test_run_passes_player_id_to_logger(self, MockLogger):
        """PIPELINE_LOGS_PLAYER_ID: run() passes player_id to AnalyticsLogger.log."""
        mock_logger_instance = MagicMock()
        MockLogger.return_value = mock_logger_instance
        pipeline = self._make_pipeline()
        pipeline.run("player_xyz", [])
        kwargs = mock_logger_instance.log.call_args.kwargs
        assert kwargs["player_id"] == "player_xyz"

    @patch("llm.seca.analysis.historical_pipeline.AnalyticsLogger")
    def test_payload_contains_all_required_keys(self, MockLogger):
        """PIPELINE_PAYLOAD_COMPLETE: Logged payload contains all four expected keys."""
        mock_logger_instance = MagicMock()
        MockLogger.return_value = mock_logger_instance
        pipeline = self._make_pipeline()
        events = [_make_event('{"opening": 0.15}')]
        pipeline.run("player1", events)
        payload = mock_logger_instance.log.call_args.kwargs["payload"]
        assert "games_analyzed" in payload
        assert "phase_rates" in payload
        assert "category_scores" in payload
        assert "dominant_category" in payload


# ---------------------------------------------------------------------------
# 26–28  EventStorage method fix
# ---------------------------------------------------------------------------


class TestEventStorageMethods:

    def test_get_recent_games_filters_by_player_id(self):
        """STORAGE_PLAYER_SCOPED: get_recent_games filters by player_id."""
        from llm.seca.events.storage import EventStorage

        db = MagicMock()
        storage = EventStorage(db)
        storage.get_recent_games(player_id="p1", limit=10)
        db.query.assert_called_once()
        # The filter_by call must include player_id
        filter_call = db.query.return_value.filter_by
        filter_call.assert_called_once_with(player_id="p1")

    def test_get_all_recent_games_has_no_player_filter(self):
        """STORAGE_GLOBAL: get_all_recent_games has no player_id filter."""
        from llm.seca.events.storage import EventStorage

        db = MagicMock()
        storage = EventStorage(db)
        storage.get_all_recent_games(limit=5)
        db.query.assert_called_once()
        # filter_by must NOT have been called with player_id
        filter_by_call = db.query.return_value.filter_by
        for c in filter_by_call.call_args_list:
            assert "player_id" not in c.kwargs

    def test_both_storage_methods_are_distinct(self):
        """STORAGE_METHODS_DISTINCT: Both methods exist and are distinct callables."""
        from llm.seca.events.storage import EventStorage

        assert hasattr(EventStorage, "get_recent_games")
        assert hasattr(EventStorage, "get_all_recent_games")
        assert EventStorage.get_recent_games is not EventStorage.get_all_recent_games


# ---------------------------------------------------------------------------
# 29  Layer boundary
# ---------------------------------------------------------------------------


class TestLayerBoundary:

    def test_historical_pipeline_has_no_forbidden_imports(self):
        """LAYER_BOUNDARY: historical_pipeline does not import brain/coach/RL modules."""
        src = (
            Path(__file__).resolve().parent.parent / "seca" / "analysis" / "historical_pipeline.py"
        )
        tree = ast.parse(src.read_text(encoding="utf-8"))
        forbidden = {"brain", "coach", "rl", "reinforcement", "world_model", "online_learner"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif node.module:
                    names = [node.module]
                for name in names:
                    for token in forbidden:
                        assert (
                            token not in name.lower()
                        ), f"historical_pipeline imports forbidden module '{name}'"
