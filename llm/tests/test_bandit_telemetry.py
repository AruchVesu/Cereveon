"""
Telemetry pins for ``llm.seca.brain.bandit.decision``.

PR #194 added structured INFO logging at the RAG retriever's empty-
retrieval branch so operators can answer "which ESV signatures hit
the no-context fallback most often" without a code change.  This
file is the analogous instrumentation pin for the LinUCB head:

  * ``select_action`` emits one log per call carrying the per-action
    UCB score breakdown (exploit / explore / total), the chosen
    action, the runner-up, and the margin between them.  Dashboards
    aggregating these can plot exploration-bonus decay over time
    (the ``explore`` term shrinks as ``A`` accumulates observations)
    and surface "which action was the bandit's runner-up most
    often" as a soft regret signal.

  * ``record_observation`` emits one log per call carrying the
    reward, action, and a post-update observation-count proxy
    (``trace(A) - n_features``).  Dashboards aggregating these can
    answer "how warmed-up is each (player, action) cell".

The pins below cover both the message format and the structured
``extra={}`` payload shape — both are downstream contract surfaces
the dashboard PR will read.

Stable test IDs (do NOT rename):
  BT_SEL_*   select_action logging pins
  BT_OBS_*   record_observation logging pins
  BT_QUIET_* paths that must NOT log (early-error exits)
"""

from __future__ import annotations

import logging
import os

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")


_LOGGER_NAME = "llm.seca.brain.bandit.decision"


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Bind the project SQLAlchemy engine to a per-test SQLite file.

    Same fixture pattern as test_bandit_decision.py — the bandit
    persistence layer is required because select_action / record_observation
    both call into the SQLAlchemy-backed repo.
    """
    from llm.tests._storage_test_helpers import bind_temp_database

    return bind_temp_database(tmp_path, monkeypatch)


# ---------------------------------------------------------------------------
# BT_SEL — select_action emits one structured INFO log per call.
# ---------------------------------------------------------------------------


def test_BT_SEL_logs_at_info_with_documented_prefix(temp_db, caplog):
    """select_action MUST emit exactly one log record with the
    documented message prefix at INFO level.  Pinned by exact-prefix
    match plus level so a future contributor cannot quietly downgrade
    to debug or remove the call."""
    from llm.seca.brain.bandit.decision import select_action

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        select_action(
            player_id="player-sel-1",
            context=[0.5, 0.3, 0.1],
            candidate_actions=["NONE", "REFLECT", "DRILL"],
        )

    selection_records = [
        rec
        for rec in caplog.records
        if rec.name == _LOGGER_NAME and rec.message.startswith("bandit select_action")
    ]
    assert len(selection_records) == 1, (
        f"Expected exactly one select_action log record; got "
        f"{[(r.levelname, r.message) for r in selection_records]!r}."
    )
    assert selection_records[0].levelno == logging.INFO, (
        f"select_action must log at INFO (operational signal, not debug), "
        f"got {selection_records[0].levelname}."
    )


def test_BT_SEL_carries_full_score_breakdown_in_extra(temp_db, caplog):
    """``extra['bandit_selection']`` MUST carry the full per-action
    score breakdown so downstream backends can index per action.
    Pins the dict shape: candidate_actions list, scores nested dict
    keyed by action, each value carrying exploit/explore/total/alpha,
    plus chosen_action / runner_up / margin top-level fields.

    The exploration-decay dashboard is downstream of the scores.X.explore
    field — losing that field would silently disable the dashboard, so
    pin it specifically.
    """
    from llm.seca.brain.bandit.decision import select_action

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        select_action(
            player_id="player-sel-2",
            context=[1.0, 0.0, 0.0],
            candidate_actions=["NONE", "REFLECT", "DRILL"],
        )

    [rec] = [
        rec
        for rec in caplog.records
        if rec.name == _LOGGER_NAME and rec.message.startswith("bandit select_action")
    ]
    payload = getattr(rec, "bandit_selection", None)
    assert isinstance(payload, dict), (
        "LogRecord is missing the structured 'bandit_selection' attribute. "
        "Confirm logger.info(..., extra={'bandit_selection': ...}) is in "
        "select_action."
    )

    # Top-level fields the dashboard PR will index on.
    expected_top_keys = {
        "player_id",
        "candidate_actions",
        "n_features",
        "scores",
        "chosen_action",
        "chosen_score",
        "runner_up_action",
        "runner_up_score",
        "margin",
    }
    assert set(payload.keys()) == expected_top_keys, (
        f"bandit_selection top-level keys drifted from documented set.  "
        f"Got {sorted(payload.keys())}, expected {sorted(expected_top_keys)}."
    )

    # Per-action nested dict — one entry per candidate, each with the
    # four documented sub-keys.
    assert set(payload["scores"].keys()) == {"NONE", "REFLECT", "DRILL"}
    for action, breakdown in payload["scores"].items():
        assert set(breakdown.keys()) == {
            "exploit",
            "explore",
            "total",
            "alpha",
        }, f"scores[{action!r}] sub-keys drifted: {sorted(breakdown.keys())}"
        # total = exploit + explore (LinUCB definition).
        assert abs(breakdown["total"] - (breakdown["exploit"] + breakdown["explore"])) < 1e-9, (
            f"scores[{action!r}]: total ({breakdown['total']}) does not equal "
            f"exploit + explore ({breakdown['exploit'] + breakdown['explore']}). "
            f"The dashboard's exploration-decay plot depends on this identity."
        )

    # Runner-up MUST be populated when there are 2+ candidates — the
    # regret-proxy dashboard depends on it.
    assert payload["runner_up_action"] is not None
    assert payload["runner_up_score"] is not None
    assert payload["margin"] is not None
    assert payload["margin"] >= 0, (
        f"margin must be non-negative (winner score - runner-up score).  "
        f"Got {payload['margin']}."
    )


def test_BT_SEL_single_candidate_omits_runner_up(temp_db, caplog):
    """When candidate_actions has length 1, runner_up_* and margin
    are None (not 0, not an empty string).  Pins the contract: the
    dashboard distinguishes "no runner-up exists" from "runner-up
    score = 0".  Without this distinction, single-candidate
    selections would appear as ultra-confident in regret plots."""
    from llm.seca.brain.bandit.decision import select_action

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        select_action(
            player_id="player-sel-3",
            context=[0.5, 0.5, 0.0],
            candidate_actions=["NONE"],
        )

    [rec] = [
        rec
        for rec in caplog.records
        if rec.name == _LOGGER_NAME and rec.message.startswith("bandit select_action")
    ]
    payload = rec.bandit_selection
    assert payload["runner_up_action"] is None
    assert payload["runner_up_score"] is None
    assert payload["margin"] is None
    assert payload["chosen_action"] == "NONE"


def test_BT_SEL_chosen_action_matches_return_value(temp_db, caplog):
    """The logged ``chosen_action`` MUST equal the function's return
    value.  Catches a future refactor that picks a different action
    than the one it logs (and vice versa) — the dashboard would
    diverge from reality."""
    from llm.seca.brain.bandit.decision import select_action

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        chosen = select_action(
            player_id="player-sel-4",
            context=[0.1, 0.2, 0.3],
            candidate_actions=["A", "B", "C"],
        )

    [rec] = [
        rec
        for rec in caplog.records
        if rec.name == _LOGGER_NAME and rec.message.startswith("bandit select_action")
    ]
    assert rec.bandit_selection["chosen_action"] == chosen


# ---------------------------------------------------------------------------
# BT_OBS — record_observation emits one structured INFO log per call.
# ---------------------------------------------------------------------------


def test_BT_OBS_logs_at_info_with_documented_prefix(temp_db, caplog):
    """record_observation MUST emit exactly one log record with the
    documented message prefix at INFO level."""
    from llm.seca.brain.bandit.decision import record_observation

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        record_observation(
            player_id="player-obs-1",
            context=[0.5, 0.3, 0.1],
            action="REFLECT",
            reward=12.5,
        )

    observation_records = [
        rec
        for rec in caplog.records
        if rec.name == _LOGGER_NAME and rec.message.startswith("bandit record_observation")
    ]
    assert len(observation_records) == 1
    assert observation_records[0].levelno == logging.INFO


def test_BT_OBS_carries_documented_payload_shape(temp_db, caplog):
    """``extra['bandit_observation']`` MUST carry the documented
    seven keys.  Future schema additions are fine but must be added
    to this test in the same commit (drift guard)."""
    from llm.seca.brain.bandit.decision import record_observation

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        record_observation(
            player_id="player-obs-2",
            context=[1.0, 0.0, 0.0],
            action="DRILL",
            reward=-3.0,
        )

    [rec] = [
        rec
        for rec in caplog.records
        if rec.name == _LOGGER_NAME and rec.message.startswith("bandit record_observation")
    ]
    payload = getattr(rec, "bandit_observation", None)
    assert isinstance(payload, dict)

    expected_keys = {
        "player_id",
        "action",
        "reward",
        "n_features",
        "alpha",
        "context_l2_norm",
        "trace_a_after",
        "observation_count_proxy",
    }
    assert set(payload.keys()) == expected_keys, (
        f"bandit_observation keys drifted.  Got {sorted(payload.keys())}, "
        f"expected {sorted(expected_keys)}."
    )

    # Type sanity — these all reach the log backend, must be JSON-
    # serializable scalars.
    assert isinstance(payload["player_id"], str)
    assert isinstance(payload["action"], str)
    assert isinstance(payload["reward"], float)
    assert payload["reward"] == -3.0  # round-trip preserves sign + magnitude
    assert isinstance(payload["n_features"], int)
    assert payload["n_features"] == 3
    assert isinstance(payload["alpha"], float)
    assert isinstance(payload["context_l2_norm"], float)
    assert isinstance(payload["trace_a_after"], float)
    assert isinstance(payload["observation_count_proxy"], float)


def test_BT_OBS_observation_count_proxy_increases_with_repeat(temp_db, caplog):
    """``observation_count_proxy = trace(A) - n_features`` MUST grow
    monotonically across repeat observations for the same (player,
    action).  This is the dashboard's "warm-up progress per cell"
    signal — if the proxy stops growing, the math regressed (e.g.,
    a future refactor stopped persisting A through the SQLite layer)."""
    from llm.seca.brain.bandit.decision import record_observation

    proxies: list[float] = []
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        for r in (1.0, 2.0, 3.0):
            caplog.clear()
            record_observation(
                player_id="player-obs-3",
                context=[1.0, 1.0, 1.0],
                action="PUZZLE",
                reward=r,
            )
            [rec] = [
                rec
                for rec in caplog.records
                if rec.name == _LOGGER_NAME and rec.message.startswith("bandit record_observation")
            ]
            proxies.append(rec.bandit_observation["observation_count_proxy"])

    # Three observations of context=[1,1,1] increment trace(A) by
    # ||x||² = 3 each time.  Cold start: trace(A) = n_features = 3
    # (A=I).  After first observation: trace = 6, proxy = 3.
    # After second: trace = 9, proxy = 6.  After third: trace = 12,
    # proxy = 9.  Strict monotonic increase.
    assert proxies == sorted(proxies), (
        f"observation_count_proxy is not monotonically non-decreasing.  "
        f"Got: {proxies}.  The persistence path is regressing."
    )
    assert proxies[0] < proxies[1] < proxies[2], (
        f"observation_count_proxy should strictly increase with each "
        f"new observation of a non-zero context.  Got: {proxies}."
    )


# ---------------------------------------------------------------------------
# BT_QUIET — paths that must NOT log (defensive, no log noise on errors).
# ---------------------------------------------------------------------------


def test_BT_QUIET_empty_candidates_does_not_log(temp_db, caplog):
    """``select_action`` with empty candidate_actions raises
    ValueError BEFORE any logging fires.  No log noise on a bad
    caller — the dashboard shouldn't see a "bandit select_action"
    line for a call that never actually selected anything."""
    from llm.seca.brain.bandit.decision import select_action

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        with pytest.raises(ValueError):
            select_action(
                player_id="player-quiet",
                context=[0.5, 0.3, 0.1],
                candidate_actions=[],
            )

    selection_records = [
        rec
        for rec in caplog.records
        if rec.name == _LOGGER_NAME and rec.message.startswith("bandit select_action")
    ]
    assert not selection_records, (
        f"select_action should not log on an empty-candidates ValueError "
        f"path.  Got: {[r.message for r in selection_records]!r}."
    )
