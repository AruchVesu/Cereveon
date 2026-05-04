"""Architecture validation tests for SECA layer boundaries.

These tests verify that layer separation rules are respected:
1. Analytics layer does not mutate brain state
2. Auth layer does not access engine logic
3. Events are properly logged to analytics
4. Brain models do not import coach/skills/adaptation layers
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _get_imports_from_source(module_path: Path) -> set[str]:
    """Parse a Python source file and return all statically imported module names."""
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
# 1. Analytics layer — does not import brain/coach/skills modules
# ---------------------------------------------------------------------------


class TestAnalyticsLayerBoundary:
    """Analytics layer must be purely write-only: AnalyticsEvent records only."""

    def test_analytics_logger_source_does_not_import_brain(self):
        """analytics/logger.py must not statically import from the brain layer."""
        path = PROJECT_ROOT / "llm/seca/analytics/logger.py"
        imports = _get_imports_from_source(path)
        brain_imports = {i for i in imports if "brain" in i}
        assert not brain_imports, (
            f"analytics/logger.py imports brain modules: {brain_imports}. "
            "Analytics must not depend on brain state."
        )

    def test_analytics_logger_source_does_not_import_skills(self):
        """analytics/logger.py must not statically import from the skills layer."""
        path = PROJECT_ROOT / "llm/seca/analytics/logger.py"
        imports = _get_imports_from_source(path)
        skills_imports = {i for i in imports if "skills" in i}
        assert not skills_imports, (
            f"analytics/logger.py imports skills modules: {skills_imports}. "
            "Analytics must not depend on the skills layer."
        )

    def test_analytics_logger_source_does_not_import_coach(self):
        """analytics/logger.py must not statically import from the coach layer."""
        path = PROJECT_ROOT / "llm/seca/analytics/logger.py"
        imports = _get_imports_from_source(path)
        coach_imports = {i for i in imports if "coach" in i}
        assert not coach_imports, (
            f"analytics/logger.py imports coach modules: {coach_imports}. "
            "Analytics must not depend on the coach layer."
        )

    def test_analytics_logger_only_adds_analytics_event(self):
        """AnalyticsLogger.log() must db.add() exactly one AnalyticsEvent — no brain models."""
        from llm.seca.analytics.logger import AnalyticsLogger
        from llm.seca.analytics.models import AnalyticsEvent

        mock_db = MagicMock()
        logger = AnalyticsLogger(mock_db)
        logger.log(event_type="game_finished", player_id="p1", payload={"result": "win"})

        assert (
            mock_db.add.call_count == 1
        ), f"Expected db.add called once, called {mock_db.add.call_count} times."
        added_obj = mock_db.add.call_args[0][0]
        assert isinstance(added_obj, AnalyticsEvent), (
            f"Expected AnalyticsEvent to be added, got {type(added_obj).__name__}. "
            "Analytics must not write brain or other model records."
        )

    def test_analytics_logger_does_not_mutate_brain_models(self):
        """AnalyticsLogger.log() must never instantiate RatingUpdate or ConfidenceUpdate."""
        from llm.seca.analytics.logger import AnalyticsLogger

        mock_db = MagicMock()
        logger = AnalyticsLogger(mock_db)
        logger.log(event_type="game_finished", player_id="p1", payload={})

        for add_call in mock_db.add.call_args_list:
            obj = add_call[0][0]
            obj_type = type(obj).__name__
            assert obj_type not in ("RatingUpdate", "ConfidenceUpdate", "BanditExperience"), (
                f"AnalyticsLogger.log() added a brain model record: {obj_type}. "
                "Analytics must not mutate brain state."
            )


# ---------------------------------------------------------------------------
# 2. Auth layer — does not access chess engine logic
# ---------------------------------------------------------------------------

_CHESS_ENGINE_PATTERNS = [
    "elite_engine_service",
    "engine_eval",
    "seca.engines",
    "stockfish",
    # "sqlalchemy" contains "create_engine" — excluded by checking the full import path
]


def _has_chess_engine_import(imports: set[str]) -> set[str]:
    """Return imports that reference chess/stockfish engine modules (not sqlalchemy)."""
    return {
        i
        for i in imports
        if any(pattern in i for pattern in _CHESS_ENGINE_PATTERNS)
        and not i.startswith("sqlalchemy")
    }


class TestAuthLayerBoundary:
    """Auth layer must not access engine or stockfish logic."""

    def test_auth_router_does_not_import_chess_engine_modules(self):
        """auth/router.py must not statically import chess engine or Stockfish modules."""
        path = PROJECT_ROOT / "llm/seca/auth/router.py"
        imports = _get_imports_from_source(path)
        bad = _has_chess_engine_import(imports)
        assert not bad, (
            f"auth/router.py imports chess engine modules: {bad}. "
            "Auth must not depend on engine logic."
        )

    def test_auth_service_does_not_import_chess_engine_modules(self):
        """auth/service.py must not statically import chess engine or Stockfish modules."""
        path = PROJECT_ROOT / "llm/seca/auth/service.py"
        assert path.exists(), "auth/service.py not found — update this test if moved"
        imports = _get_imports_from_source(path)
        bad = _has_chess_engine_import(imports)
        assert not bad, (
            f"auth/service.py imports chess engine modules: {bad}. "
            "Auth must not depend on engine logic."
        )

    def test_auth_tokens_does_not_import_chess_engine_modules(self):
        """auth/tokens.py must not statically import chess engine modules."""
        path = PROJECT_ROOT / "llm/seca/auth/tokens.py"
        if not path.exists():
            pytest.skip("auth/tokens.py not found")
        imports = _get_imports_from_source(path)
        bad = _has_chess_engine_import(imports)
        assert not bad, f"auth/tokens.py imports chess engine modules: {bad}."

    def test_all_auth_source_files_free_of_engine_imports(self):
        """Every .py file in llm/seca/auth/ must not import from llm.seca.engines.*"""
        auth_dir = PROJECT_ROOT / "llm/seca/auth"
        sources = [p for p in auth_dir.glob("*.py") if p.name != "__init__.py"]
        assert sources, "No auth source files found — check the path"
        violations = {}
        for path in sources:
            imports = _get_imports_from_source(path)
            seca_engine_imports = {i for i in imports if "seca.engines" in i}
            if seca_engine_imports:
                violations[path.name] = seca_engine_imports
        assert not violations, f"Auth layer files import SECA engine modules: {violations}."


# ---------------------------------------------------------------------------
# 3. Events layer — store_game() logs to analytics
# ---------------------------------------------------------------------------


class TestEventStorageLogging:
    """EventStorage.store_game() must call AnalyticsLogger with the correct arguments."""

    def _make_mock_db(self) -> MagicMock:
        mock_db = MagicMock()
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()
        mock_db.refresh = MagicMock()
        return mock_db

    def test_store_game_calls_analytics_logger(self):
        """store_game() must instantiate and call AnalyticsLogger exactly once."""
        from llm.seca.events.storage import EventStorage

        mock_db = self._make_mock_db()
        with patch("llm.seca.events.storage.AnalyticsLogger") as MockLogger:
            mock_instance = MagicMock()
            MockLogger.return_value = mock_instance

            EventStorage(mock_db).store_game(
                player_id="player-1",
                pgn="1. e4 e5",
                result="win",
                accuracy=0.85,
                weaknesses={"endgame": 0.3},
            )

            MockLogger.assert_called_once_with(mock_db)
            mock_instance.log.assert_called_once()

    def test_store_game_logs_game_finished_event_type(self):
        """store_game() must log with EventType.GAME_FINISHED."""
        from llm.seca.events.storage import EventStorage
        from llm.seca.analytics.events import EventType

        mock_db = self._make_mock_db()
        with patch("llm.seca.events.storage.AnalyticsLogger") as MockLogger:
            mock_instance = MagicMock()
            MockLogger.return_value = mock_instance

            EventStorage(mock_db).store_game(
                player_id="player-1",
                pgn="1. e4 e5",
                result="draw",
                accuracy=0.70,
                weaknesses={},
            )

            call_kwargs = mock_instance.log.call_args
            # Support both positional and keyword call styles
            logged_event_type = (
                call_kwargs.kwargs.get("event_type") if call_kwargs.kwargs else call_kwargs.args[0]
            )
            assert logged_event_type == EventType.GAME_FINISHED, (
                f"Expected EventType.GAME_FINISHED ('{EventType.GAME_FINISHED}'), "
                f"got {logged_event_type!r}"
            )

    def test_store_game_passes_player_id_to_logger(self):
        """store_game() must pass the player_id to AnalyticsLogger.log."""
        from llm.seca.events.storage import EventStorage

        mock_db = self._make_mock_db()
        with patch("llm.seca.events.storage.AnalyticsLogger") as MockLogger:
            mock_instance = MagicMock()
            MockLogger.return_value = mock_instance

            EventStorage(mock_db).store_game(
                player_id="player-42",
                pgn="1. d4 d5",
                result="loss",
                accuracy=0.50,
                weaknesses={"tactics": 0.8},
            )

            call_kwargs = mock_instance.log.call_args
            logged_player_id = call_kwargs.kwargs.get("player_id")
            assert (
                logged_player_id == "player-42"
            ), f"Expected player_id='player-42', got {logged_player_id!r}"

    def test_store_game_includes_result_in_payload(self):
        """store_game() must include the game result in the analytics payload."""
        from llm.seca.events.storage import EventStorage

        mock_db = self._make_mock_db()
        with patch("llm.seca.events.storage.AnalyticsLogger") as MockLogger:
            mock_instance = MagicMock()
            MockLogger.return_value = mock_instance

            EventStorage(mock_db).store_game(
                player_id="player-1",
                pgn="1. e4 c5",
                result="win",
                accuracy=0.90,
                weaknesses={},
            )

            call_kwargs = mock_instance.log.call_args
            payload = call_kwargs.kwargs.get("payload") or {}
            assert (
                payload.get("result") == "win"
            ), f"Expected payload['result']='win', got {payload!r}"

    def test_store_game_includes_accuracy_in_payload(self):
        """store_game() must include accuracy in the analytics payload."""
        from llm.seca.events.storage import EventStorage

        mock_db = self._make_mock_db()
        with patch("llm.seca.events.storage.AnalyticsLogger") as MockLogger:
            mock_instance = MagicMock()
            MockLogger.return_value = mock_instance

            EventStorage(mock_db).store_game(
                player_id="player-1",
                pgn="1. e4 c5",
                result="loss",
                accuracy=0.62,
                weaknesses={},
            )

            call_kwargs = mock_instance.log.call_args
            payload = call_kwargs.kwargs.get("payload") or {}
            assert "accuracy" in payload, f"Expected payload to contain 'accuracy', got {payload!r}"
            assert payload["accuracy"] == pytest.approx(0.62)


# ---------------------------------------------------------------------------
# 4. Brain models — do not import coach/skills/adaptation/analytics/engine
# ---------------------------------------------------------------------------


class TestBrainModelBoundary:
    """Brain models must only depend on auth.models (shared Base) and SQLAlchemy."""

    def test_brain_models_do_not_import_coach(self):
        path = PROJECT_ROOT / "llm/seca/brain/models.py"
        imports = _get_imports_from_source(path)
        bad = {i for i in imports if "coach" in i}
        assert not bad, (
            f"brain/models.py imports coach modules: {bad}. "
            "Brain models must not depend on the coach layer."
        )

    def test_brain_models_do_not_import_skills(self):
        path = PROJECT_ROOT / "llm/seca/brain/models.py"
        imports = _get_imports_from_source(path)
        bad = {i for i in imports if "skills" in i}
        assert not bad, (
            f"brain/models.py imports skills modules: {bad}. "
            "Brain models must not depend on the skills layer."
        )

    def test_brain_models_do_not_import_analytics(self):
        path = PROJECT_ROOT / "llm/seca/brain/models.py"
        imports = _get_imports_from_source(path)
        bad = {i for i in imports if "analytics" in i}
        assert not bad, (
            f"brain/models.py imports analytics modules: {bad}. "
            "Brain models must not depend on the analytics layer."
        )

    def test_brain_models_do_not_import_events_layer(self):
        path = PROJECT_ROOT / "llm/seca/brain/models.py"
        imports = _get_imports_from_source(path)
        # Allow auth.models (needed for Base); disallow events
        bad = {i for i in imports if "events" in i and "auth" not in i}
        assert not bad, (
            f"brain/models.py imports events modules: {bad}. "
            "Brain models must not depend on the events layer."
        )

    def test_brain_models_do_not_import_chess_engine(self):
        path = PROJECT_ROOT / "llm/seca/brain/models.py"
        imports = _get_imports_from_source(path)
        bad = _has_chess_engine_import(imports)
        assert not bad, f"brain/models.py imports chess engine modules: {bad}."
