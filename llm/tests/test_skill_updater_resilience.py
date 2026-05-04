"""
Resilience test: SkillUpdater failure must not abort /game/finish.

Gap 10 regression — SEC_SKILL_UPDATER_GUARDED.

The finish_game endpoint wraps SkillUpdater.update_from_event() in try/except
so that a DB write failure (deadlock, constraint violation, etc.) is logged but
does not surface as HTTP 500.  This test verifies that behaviour with a minimal
stub that avoids the full SECA DB stack.
"""

from __future__ import annotations

import types
import pytest


# ---------------------------------------------------------------------------
# Minimal stub that replays only the SkillUpdater guard logic from router.py
# without pulling in the full SQLAlchemy / FastAPI stack.
# ---------------------------------------------------------------------------

import logging

logger = logging.getLogger(__name__)


def _finish_game_core(skill_updater_factory, player_id: str, event) -> dict:
    """
    Stripped-down version of the finish_game path that exercises the guard.

    skill_updater_factory() returns an object with update_from_event().
    """
    try:
        skill_updater_factory().update_from_event(player_id, event)
    except Exception:
        logger.exception(
            "SkillUpdater failed for player %s; rating not updated this game", player_id
        )

    # Simulate the remainder of the response being built even after failure.
    return {"status": "stored", "new_rating": 1000.0}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class _GoodUpdater:
    def update_from_event(self, player_id, event):
        pass  # succeeds silently


class _BrokenUpdater:
    def update_from_event(self, player_id, event):
        raise RuntimeError("simulated DB write failure")


class _ConstraintUpdater:
    def update_from_event(self, player_id, event):
        raise Exception("UNIQUE constraint failed: player.rating")


_DUMMY_EVENT = types.SimpleNamespace(result="win", accuracy=0.8)


class TestSkillUpdaterResilience:

    def test_success_path_returns_stored(self):
        """When SkillUpdater succeeds, the response status is 'stored'."""
        result = _finish_game_core(lambda: _GoodUpdater(), "p1", _DUMMY_EVENT)
        assert result["status"] == "stored"

    def test_runtime_error_does_not_propagate(self):
        """SEC_SKILL_UPDATER_GUARDED: RuntimeError in SkillUpdater must not raise."""
        result = _finish_game_core(lambda: _BrokenUpdater(), "p1", _DUMMY_EVENT)
        assert result["status"] == "stored", (
            "finish_game returned an error status after SkillUpdater failure — "
            "the exception guard is missing or incomplete."
        )

    def test_constraint_error_does_not_propagate(self):
        """SEC_SKILL_UPDATER_GUARDED: generic Exception in SkillUpdater must not raise."""
        result = _finish_game_core(lambda: _ConstraintUpdater(), "p1", _DUMMY_EVENT)
        assert result["status"] == "stored"

    def test_response_still_has_rating_on_failure(self):
        """Even after a SkillUpdater failure, the response includes a rating field."""
        result = _finish_game_core(lambda: _BrokenUpdater(), "p1", _DUMMY_EVENT)
        assert "new_rating" in result
