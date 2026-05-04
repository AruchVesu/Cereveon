"""
Unit tests for the LinUCB decision module
(seca/brain/bandit/decision.py) — SECA v1's deferred action-selection
substrate.

Pinned invariants
-----------------
 1. INIT_COLD_START:           a never-seen player+action initialises
                                A=I, b=0, so the first observation
                                drives a meaningful UCB score.
 2. SELECT_DETERMINISTIC:      same context + same stored weights →
                                same action chosen, every time.
 3. SELECT_TIE_BREAK:          tied UCB scores break by candidate
                                order (first-listed wins).
 4. RECORD_INCREMENTS_A:       A_new = A_old + x xᵀ exactly.
 5. RECORD_INCREMENTS_B:       b_new = b_old + r·x exactly.
 6. RECORD_PERSISTS:           a record_observation() call survives
                                a process restart (DB roundtrip).
 7. RESET_SCOPED:              reset_player(action) clears one row;
                                reset_player(None) clears all rows
                                for that player.
 8. PER_PLAYER_ISOLATION:      player A's weights don't influence
                                player B's selection.
 9. FEATURE_DIM_MISMATCH:      stored row with different n_features
                                is treated as cold-start (defensive
                                against schema drift).
"""

from __future__ import annotations

import os
import sqlite3

import numpy as np
import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Same temp-db pattern as test_game_checkpoint /
    test_repertoire_endpoint."""
    db_file = tmp_path / "seca-bandit-test.db"
    monkeypatch.setattr("llm.seca.storage.db.DB_PATH", db_file)
    from llm.seca.storage.db import init_db
    init_db()
    yield db_file


# ---------------------------------------------------------------------------
# 1.  Cold-start + serialisation roundtrip
# ---------------------------------------------------------------------------


class TestColdStartAndPersistence:
    def test_select_with_no_stored_weights(self, temp_db):
        """INIT_COLD_START — every action returns identity-A scoring
        equally; tie breaks by candidate order."""
        from llm.seca.brain.bandit.decision import select_action
        ctx = [0.5, 0.3, 0.1]
        chosen = select_action(
            player_id="player-cold",
            context=ctx,
            candidate_actions=["a", "b", "c"],
        )
        # All three actions cold-start identically — first-listed wins.
        assert chosen == "a"

    def test_record_persists_to_db(self, temp_db):
        """RECORD_PERSISTS — verify the observation lands in the
        bandit_weights table by reading back through the repo helper."""
        from llm.seca.brain.bandit.decision import record_observation
        from llm.seca.storage.repo import load_bandit_weights

        record_observation(
            player_id="player-rec",
            context=[1.0, 0.0, 0.0],
            action="reflect",
            reward=0.5,
        )
        row = load_bandit_weights("player-rec", "reflect")
        assert row is not None
        assert row["n_features"] == 3

    def test_record_increments_A_and_b(self, temp_db):
        """RECORD_INCREMENTS_A + RECORD_INCREMENTS_B — closed-form
        increment math: A_new = A_old + x xᵀ; b_new = b_old + r·x."""
        from llm.seca.brain.bandit.decision import record_observation
        from llm.seca.storage.repo import load_bandit_weights
        import json

        ctx = [1.0, 0.0, 0.0]
        record_observation(
            player_id="p1",
            context=ctx,
            action="drill",
            reward=2.0,
        )

        row = load_bandit_weights("p1", "drill")
        A = np.array(json.loads(row["A_json"]))
        b = np.array(json.loads(row["b_json"]))

        # Cold-start A was identity, b was zero.  After one obs:
        #   A_new = I + x xᵀ where x = [1,0,0]ᵀ
        #         = I + diag([1,0,0])  (i.e. only the [0,0] cell shifts)
        #   b_new = 0 + 2·x = [2,0,0]ᵀ
        expected_A = np.eye(3) + np.array([[1, 0, 0], [0, 0, 0], [0, 0, 0]], dtype=float)
        expected_b = np.array([[2.0], [0.0], [0.0]])
        np.testing.assert_allclose(A, expected_A, atol=1e-9)
        np.testing.assert_allclose(b, expected_b, atol=1e-9)


# ---------------------------------------------------------------------------
# 2.  Action selection determinism + tie-breaking
# ---------------------------------------------------------------------------


class TestActionSelection:
    def test_deterministic_under_same_inputs(self, temp_db):
        """SELECT_DETERMINISTIC — same context + same stored state →
        same chosen action, every time."""
        from llm.seca.brain.bandit.decision import (
            select_action,
            record_observation,
        )

        # Drive one action's reward up so it has a real preference.
        for _ in range(5):
            record_observation("p", [1.0, 0.0], "high", reward=1.0)
            record_observation("p", [1.0, 0.0], "low", reward=-1.0)

        chosen_first = select_action("p", [1.0, 0.0], ["high", "low"])
        chosen_second = select_action("p", [1.0, 0.0], ["high", "low"])
        chosen_third = select_action("p", [1.0, 0.0], ["high", "low"])

        assert chosen_first == chosen_second == chosen_third == "high"

    def test_reward_signal_drives_selection(self, temp_db):
        """A handful of high-reward observations on action X should
        promote X over an untouched alternative."""
        from llm.seca.brain.bandit.decision import (
            select_action,
            record_observation,
        )

        ctx = [1.0, 0.5]
        for _ in range(10):
            record_observation("p", ctx, "good", reward=1.0)
            record_observation("p", ctx, "bad", reward=-1.0)

        chosen = select_action("p", ctx, ["good", "bad"])
        assert chosen == "good"

    def test_tie_break_first_listed(self, temp_db):
        """SELECT_TIE_BREAK — when no action has been observed, every
        action ties; the first one in candidate_actions wins."""
        from llm.seca.brain.bandit.decision import select_action
        ctx = [0.1, 0.2, 0.3]

        # Different orderings produce different winners.
        first_a = select_action("untouched", ctx, ["a", "b", "c"])
        first_b = select_action("untouched", ctx, ["b", "a", "c"])

        assert first_a == "a"
        assert first_b == "b"

    def test_empty_candidate_list_raises(self, temp_db):
        from llm.seca.brain.bandit.decision import select_action
        with pytest.raises(ValueError, match="candidate_actions must not be empty"):
            select_action("p", [1.0, 0.0], [])


# ---------------------------------------------------------------------------
# 3.  Per-player isolation + feature-dim mismatch
# ---------------------------------------------------------------------------


class TestIsolationAndDefence:
    def test_per_player_isolation(self, temp_db):
        """PER_PLAYER_ISOLATION — player A's observations don't bleed
        into player B's selection."""
        from llm.seca.brain.bandit.decision import (
            select_action,
            record_observation,
        )

        # A trains "high" hard.
        for _ in range(20):
            record_observation("alice", [1.0, 0.0], "high", reward=5.0)
            record_observation("alice", [1.0, 0.0], "low", reward=-5.0)

        # B is a fresh player — should see the cold-start tie-break,
        # NOT alice's preferences.
        chosen_b = select_action("bob", [1.0, 0.0], ["low", "high"])
        assert chosen_b == "low"  # first-listed in cold-start tie-break

    def test_feature_dim_mismatch_treated_as_cold_start(self, temp_db):
        """FEATURE_DIM_MISMATCH — if the caller's feature vector
        size doesn't match the stored row's n_features (e.g. the
        builder added a feature), the stored row is ignored and
        treated as cold-start.  Defensive against schema drift."""
        from llm.seca.brain.bandit.decision import (
            select_action,
            record_observation,
        )

        # Record with 2 features.
        record_observation("p", [1.0, 1.0], "a", reward=10.0)
        # Now select with a 3-feature context — stored row's
        # n_features=2 doesn't match → treat as cold-start.
        chosen = select_action("p", [1.0, 1.0, 1.0], ["a", "b"])
        # All cold-start → first-listed wins.
        assert chosen == "a"


# ---------------------------------------------------------------------------
# 4.  Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_single_action(self, temp_db):
        """RESET_SCOPED — reset_player(action) drops one row only."""
        from llm.seca.brain.bandit.decision import (
            record_observation,
            reset_player,
        )
        from llm.seca.storage.repo import load_bandit_weights

        record_observation("p", [1.0, 0.0], "x", reward=1.0)
        record_observation("p", [1.0, 0.0], "y", reward=1.0)

        reset_player("p", "x")

        assert load_bandit_weights("p", "x") is None
        assert load_bandit_weights("p", "y") is not None

    def test_reset_whole_player(self, temp_db):
        """RESET_SCOPED whole-player variant — reset_player(None)
        clears every action for that player."""
        from llm.seca.brain.bandit.decision import (
            record_observation,
            reset_player,
        )
        from llm.seca.storage.repo import load_bandit_weights

        record_observation("p", [1.0, 0.0], "x", reward=1.0)
        record_observation("p", [1.0, 0.0], "y", reward=1.0)
        record_observation("p", [1.0, 0.0], "z", reward=1.0)

        reset_player("p", None)

        assert load_bandit_weights("p", "x") is None
        assert load_bandit_weights("p", "y") is None
        assert load_bandit_weights("p", "z") is None

    def test_reset_does_not_affect_other_players(self, temp_db):
        from llm.seca.brain.bandit.decision import (
            record_observation,
            reset_player,
        )
        from llm.seca.storage.repo import load_bandit_weights

        record_observation("alice", [1.0], "x", reward=1.0)
        record_observation("bob",   [1.0], "x", reward=1.0)

        reset_player("alice", None)

        assert load_bandit_weights("alice", "x") is None
        assert load_bandit_weights("bob", "x") is not None
