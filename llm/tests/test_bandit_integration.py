"""
Integration tests for the bandit decision step wired into the
/game/finish handler.

Pinned invariants
-----------------
 1. FLAG_OFF_DETERMINISTIC: with SECA_USE_BANDIT_COACH unset (or "0"),
    the user-visible coach_action is exactly what the deterministic
    PostGameCoachController returned.  Live behaviour unchanged
    until the flag is explicitly flipped.
 2. FLAG_OFF_STILL_OBSERVES: even with the flag off, the bandit
    records the (context, action, reward) observation — that's the
    warm-up phase before anyone can flip the flag in production.
 3. FLAG_ON_OVERRIDES: with SECA_USE_BANDIT_COACH=1, the user-visible
    coach_action.type comes from bandit.select_action; the action's
    `reason` field documents the override ("bandit:linucb …").
 4. BANDIT_FAILURE_FALLS_BACK: any exception inside the bandit
    pipeline (selection or observation) returns the deterministic
    action unchanged.  /game/finish must never break because of a
    misbehaving bandit.
 5. ACTION_SPACE_LOCKED: candidate_actions matches PostGameCoach's
    rule outputs exactly (NONE / REFLECT / DRILL / PUZZLE /
    PLAN_UPDATE).  Drift here would fail the bandit's
    feature-dim sanity check at select_action time.
"""

from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """In-memory style fixture (file-backed for the raw-sqlite
    repo helpers).  Same pattern as test_bandit_decision."""
    db_file = tmp_path / "seca-bandit-int.db"
    monkeypatch.setattr("llm.seca.storage.db.DB_PATH", db_file)
    from llm.seca.storage.db import init_db
    init_db()
    yield db_file


def _make_deterministic_action(action_type="DRILL", weakness="tactics", reason="rule-1"):
    return SimpleNamespace(type=action_type, weakness=weakness, reason=reason)


# ---------------------------------------------------------------------------
# 1.  Action space matches the bandit's expectations
# ---------------------------------------------------------------------------


def test_action_space_locked():
    """ACTION_SPACE_LOCKED — _BANDIT_ACTIONS must mirror
    PostGameCoachController's rule outputs.  Drift here would mean
    the bandit ranks actions the deterministic controller can't
    produce, or vice versa."""
    from llm.seca.events.router import _BANDIT_ACTIONS

    # PostGameCoachController.decide branches on these types
    # (live_controller.py L40+); keep aligned.
    expected = {"NONE", "REFLECT", "DRILL", "PUZZLE", "PLAN_UPDATE"}
    assert set(_BANDIT_ACTIONS) == expected


# ---------------------------------------------------------------------------
# 2.  Flag-off path: deterministic action passed through, observation logged
# ---------------------------------------------------------------------------


class TestFlagOffPath:
    def test_returns_deterministic_action(self, temp_db, monkeypatch):
        """FLAG_OFF_DETERMINISTIC."""
        monkeypatch.delenv("SECA_USE_BANDIT_COACH", raising=False)
        from llm.seca.events.router import _apply_bandit_decision

        det = _make_deterministic_action(action_type="REFLECT")
        result = _apply_bandit_decision(
            player_id="p-flag-off",
            deterministic_action=det,
            rating_before=1500.0,
            confidence_before=0.5,
            accuracy=0.8,
            weaknesses={"tactics": 0.6},
            reward=10.0,
        )
        assert result is det  # exact same object — no override

    def test_records_observation_for_warmup(self, temp_db, monkeypatch):
        """FLAG_OFF_STILL_OBSERVES — the bandit's `bandit_weights`
        row gets populated for the deterministic-chosen action."""
        monkeypatch.delenv("SECA_USE_BANDIT_COACH", raising=False)
        from llm.seca.events.router import _apply_bandit_decision
        from llm.seca.storage.repo import load_bandit_weights

        det = _make_deterministic_action(action_type="DRILL")
        _apply_bandit_decision(
            player_id="p-warmup",
            deterministic_action=det,
            rating_before=1200.0,
            confidence_before=0.4,
            accuracy=0.7,
            weaknesses={"tactics": 0.3},
            reward=5.0,
        )

        # bandit_weights now has a DRILL row for this player.
        row = load_bandit_weights("p-warmup", "DRILL")
        assert row is not None
        assert row["n_features"] == 6  # build_context_vector returns 6 features


# ---------------------------------------------------------------------------
# 3.  Flag-on path: bandit selection wins
# ---------------------------------------------------------------------------


class TestFlagOnPath:
    def test_overrides_deterministic_with_bandit_pick(self, temp_db, monkeypatch):
        """FLAG_ON_OVERRIDES."""
        monkeypatch.setenv("SECA_USE_BANDIT_COACH", "1")
        from llm.seca.events.router import _apply_bandit_decision

        # Stub the bandit's select_action to deterministically
        # return PUZZLE so we can assert override happened.
        with patch(
            "llm.seca.brain.bandit.decision.select_action",
            return_value="PUZZLE",
        ):
            det = _make_deterministic_action(action_type="DRILL", weakness="tactics")
            result = _apply_bandit_decision(
                player_id="p-flag-on",
                deterministic_action=det,
                rating_before=1500.0,
                confidence_before=0.5,
                accuracy=0.8,
                weaknesses={"tactics": 0.5},
                reward=8.0,
            )
        assert result.type == "PUZZLE"
        # Weakness preserved from the deterministic action — bandit
        # doesn't have the game-specific weakness label.
        assert result.weakness == "tactics"
        # Reason documents the override.
        assert "bandit:linucb" in result.reason

    def test_observation_still_logged_when_flag_on(self, temp_db, monkeypatch):
        """Even when bandit overrides, the observation is from the
        DETERMINISTIC action.  Otherwise the bandit would only ever
        learn from its own choices — a cold-start bootstrap problem
        that the warm-up design exists to avoid."""
        monkeypatch.setenv("SECA_USE_BANDIT_COACH", "1")
        from llm.seca.events.router import _apply_bandit_decision
        from llm.seca.storage.repo import load_bandit_weights

        with patch(
            "llm.seca.brain.bandit.decision.select_action",
            return_value="PUZZLE",
        ):
            det = _make_deterministic_action(action_type="REFLECT")
            _apply_bandit_decision(
                player_id="p-on-obs",
                deterministic_action=det,
                rating_before=1500.0,
                confidence_before=0.5,
                accuracy=0.8,
                weaknesses={"tactics": 0.5},
                reward=12.0,
            )

        # Observation goes against the deterministic action (REFLECT),
        # not the bandit's override.
        reflect_row = load_bandit_weights("p-on-obs", "REFLECT")
        puzzle_row = load_bandit_weights("p-on-obs", "PUZZLE")
        assert reflect_row is not None, "deterministic action's row missing"
        assert puzzle_row is None, "bandit's override-action shouldn't have an observation"


# ---------------------------------------------------------------------------
# 4.  Defensive fallback on any pipeline failure
# ---------------------------------------------------------------------------


class TestDefensiveFallback:
    def test_select_action_failure_falls_back_to_deterministic(self, temp_db, monkeypatch):
        """BANDIT_FAILURE_FALLS_BACK — bandit blowing up must not
        fail /game/finish."""
        monkeypatch.setenv("SECA_USE_BANDIT_COACH", "1")
        from llm.seca.events.router import _apply_bandit_decision

        with patch(
            "llm.seca.brain.bandit.decision.select_action",
            side_effect=RuntimeError("bandit imploded"),
        ):
            det = _make_deterministic_action(action_type="REFLECT")
            result = _apply_bandit_decision(
                player_id="p-fallback",
                deterministic_action=det,
                rating_before=1500.0,
                confidence_before=0.5,
                accuracy=0.8,
                weaknesses={"tactics": 0.5},
                reward=10.0,
            )
        assert result is det  # untouched fallback

    def test_observation_failure_does_not_propagate(self, temp_db, monkeypatch):
        """Non-fatal observation failure — even if record_observation
        crashes, the request flow keeps the deterministic action."""
        monkeypatch.delenv("SECA_USE_BANDIT_COACH", raising=False)
        from llm.seca.events.router import _apply_bandit_decision

        with patch(
            "llm.seca.brain.bandit.decision.record_observation",
            side_effect=RuntimeError("DB locked"),
        ):
            det = _make_deterministic_action(action_type="REFLECT")
            result = _apply_bandit_decision(
                player_id="p-obs-fail",
                deterministic_action=det,
                rating_before=1500.0,
                confidence_before=0.5,
                accuracy=0.8,
                weaknesses={"tactics": 0.5},
                reward=10.0,
            )
        assert result is det
