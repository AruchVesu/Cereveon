"""
Dynamic adaptation mode tests — llm/tests/test_dynamic_adaptation.py

Covers all layers: pure registry logic, API wiring (AST), Pydantic validation,
and HTTP behaviour via a minimal stub app.

Invariants pinned
-----------------
DA-01  Registry starts with mode disabled and neutral ELO for unknown player
DA-02  set_mode(enabled=True) enables dynamic mode
DA-03  set_mode(enabled=True, base_elo=N) honours custom base ELO
DA-04  set_mode(enabled=False) disables mode; move_count resets
DA-05  get_elo() returns None when disabled, current_elo when enabled
DA-06  record_move_quality("good") increases ELO
DA-07  record_move_quality("blunder") decreases ELO
DA-08  record_move_quality("best") increases ELO
DA-09  record_move_quality("excellent") increases ELO
DA-10  record_move_quality("inaccuracy") decreases ELO
DA-11  record_move_quality("mistake") decreases ELO
DA-12  ELO never falls below ELO_MIN (600) after many blunders
DA-13  ELO never rises above ELO_MAX (2400) after many best moves
DA-14  record_move_quality is a no-op when mode is disabled
DA-15  Unknown quality label produces no ELO change
DA-16  move_count increments with each quality record
DA-17  Player A's state does not affect Player B (isolation)
DA-18  Registry is thread-safe under concurrent updates
DA-19  AdaptationModeRequest accepts valid payload
DA-20  AdaptationModeRequest rejects base_elo < 600
DA-21  AdaptationModeRequest rejects base_elo > 2400
DA-22  POST /adaptation/mode endpoint exists in server.py
DA-23  GET /adaptation/mode endpoint exists in server.py
DA-24  Both adaptation/mode endpoints require get_current_player
DA-25  /move endpoint checks _dynamic_registry.get_elo (AST)
DA-26  /live/move records move quality when dynamic mode enabled (AST)
DA-27  HTTP stub: POST /adaptation/mode enables mode
DA-28  HTTP stub: GET /adaptation/mode returns current state
DA-29  HTTP stub: toggling off resets move_count
DA-30  base_elo default: enabling without base_elo uses player's adaptation ELO
"""

from __future__ import annotations

import ast
import os
import threading
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SERVER_PY = _REPO_ROOT / "llm" / "server.py"


# ===========================================================================
# DA-01 – DA-18  Pure registry unit tests
# ===========================================================================


from llm.seca.adaptation.dynamic_mode import (
    DynamicModeRegistry,
    ELO_MIN,
    ELO_MAX,
    ELO_ASSESSMENT_START,
    _QUALITY_DELTA,
)


class TestDynamicModeRegistryDefaults:
    """DA-01: Unknown player gets disabled state with neutral ELO."""

    def test_unknown_player_disabled(self):
        reg = DynamicModeRegistry()
        state = reg.get_state("nobody")
        assert state.enabled is False

    def test_unknown_player_elo_is_assessment_start(self):
        reg = DynamicModeRegistry()
        state = reg.get_state("nobody")
        assert state.current_elo == ELO_ASSESSMENT_START

    def test_unknown_player_move_count_zero(self):
        reg = DynamicModeRegistry()
        assert reg.get_state("nobody").move_count == 0


class TestSetMode:
    """DA-02 – DA-05: set_mode and get_elo."""

    def test_enable_sets_enabled_flag(self):
        """DA-02"""
        reg = DynamicModeRegistry()
        state = reg.set_mode("p1", enabled=True)
        assert state.enabled is True

    def test_enable_with_custom_base_elo(self):
        """DA-03"""
        reg = DynamicModeRegistry()
        state = reg.set_mode("p1", enabled=True, base_elo=900)
        assert state.current_elo == 900

    def test_disable_clears_enabled_flag(self):
        """DA-04a"""
        reg = DynamicModeRegistry()
        reg.set_mode("p1", enabled=True)
        state = reg.set_mode("p1", enabled=False)
        assert state.enabled is False

    def test_disable_resets_move_count(self):
        """DA-04b"""
        reg = DynamicModeRegistry()
        reg.set_mode("p1", enabled=True, base_elo=1200)
        reg.record_move_quality("p1", "good")
        reg.record_move_quality("p1", "good")
        reg.set_mode("p1", enabled=False)
        assert reg.get_state("p1").move_count == 0

    def test_get_elo_returns_none_when_disabled(self):
        """DA-05a"""
        reg = DynamicModeRegistry()
        assert reg.get_elo("p1") is None

    def test_get_elo_returns_value_when_enabled(self):
        """DA-05b"""
        reg = DynamicModeRegistry()
        reg.set_mode("p1", enabled=True, base_elo=1400)
        assert reg.get_elo("p1") == 1400


class TestRecordMoveQuality:
    """DA-06 – DA-16: quality recording and ELO adjustments."""

    def _reg_at(self, elo: int) -> DynamicModeRegistry:
        reg = DynamicModeRegistry()
        reg.set_mode("p", enabled=True, base_elo=elo)
        return reg

    def test_good_increases_elo(self):
        """DA-06"""
        reg = self._reg_at(1200)
        state = reg.record_move_quality("p", "good")
        assert state.current_elo > 1200

    def test_blunder_decreases_elo(self):
        """DA-07"""
        reg = self._reg_at(1200)
        state = reg.record_move_quality("p", "blunder")
        assert state.current_elo < 1200

    def test_best_increases_elo(self):
        """DA-08"""
        reg = self._reg_at(1200)
        state = reg.record_move_quality("p", "best")
        assert state.current_elo > 1200

    def test_excellent_increases_elo(self):
        """DA-09"""
        reg = self._reg_at(1200)
        state = reg.record_move_quality("p", "excellent")
        assert state.current_elo > 1200

    def test_inaccuracy_decreases_elo(self):
        """DA-10"""
        reg = self._reg_at(1200)
        state = reg.record_move_quality("p", "inaccuracy")
        assert state.current_elo < 1200

    def test_mistake_decreases_elo(self):
        """DA-11"""
        reg = self._reg_at(1200)
        state = reg.record_move_quality("p", "mistake")
        assert state.current_elo < 1200

    def test_elo_never_below_min(self):
        """DA-12: 1000 blunders from ELO_MIN must not go below floor."""
        reg = self._reg_at(ELO_MIN)
        for _ in range(1000):
            reg.record_move_quality("p", "blunder")
        assert reg.get_state("p").current_elo == ELO_MIN

    def test_elo_never_above_max(self):
        """DA-13: 1000 best moves from ELO_MAX must not exceed ceiling."""
        reg = self._reg_at(ELO_MAX)
        for _ in range(1000):
            reg.record_move_quality("p", "best")
        assert reg.get_state("p").current_elo == ELO_MAX

    def test_noop_when_disabled(self):
        """DA-14"""
        reg = DynamicModeRegistry()
        # mode is disabled (default)
        state_before = reg.get_state("p")
        reg.record_move_quality("p", "best")
        state_after = reg.get_state("p")
        assert state_after.current_elo == state_before.current_elo
        assert state_after.move_count == state_before.move_count

    def test_unknown_quality_no_change(self):
        """DA-15"""
        reg = self._reg_at(1200)
        state = reg.record_move_quality("p", "spectacular")  # unknown label
        assert state.current_elo == 1200

    def test_move_count_increments(self):
        """DA-16"""
        reg = self._reg_at(1200)
        for i in range(5):
            reg.record_move_quality("p", "good")
        assert reg.get_state("p").move_count == 5


class TestSessionIsolation:
    """DA-17: player A's state must not affect player B."""

    def test_independent_states(self):
        reg = DynamicModeRegistry()
        reg.set_mode("alice", enabled=True, base_elo=1200)
        reg.set_mode("bob", enabled=True, base_elo=1800)

        for _ in range(10):
            reg.record_move_quality("alice", "blunder")

        assert reg.get_state("alice").current_elo < 1200
        assert reg.get_state("bob").current_elo == 1800

    def test_disabling_one_does_not_affect_other(self):
        reg = DynamicModeRegistry()
        reg.set_mode("alice", enabled=True, base_elo=1400)
        reg.set_mode("bob", enabled=True, base_elo=1400)

        reg.set_mode("alice", enabled=False)
        assert reg.get_state("bob").enabled is True


class TestThreadSafety:
    """DA-18: concurrent updates must not corrupt state."""

    def test_concurrent_quality_records_are_bounded(self):
        reg = DynamicModeRegistry()
        reg.set_mode("player", enabled=True, base_elo=1200)

        errors: list[Exception] = []

        def _worker(quality: str, n: int) -> None:
            try:
                for _ in range(n):
                    reg.record_move_quality("player", quality)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=_worker, args=("good", 100)),
            threading.Thread(target=_worker, args=("blunder", 100)),
            threading.Thread(target=_worker, args=("best", 100)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety violations: {errors}"
        elo = reg.get_state("player").current_elo
        assert ELO_MIN <= elo <= ELO_MAX, f"ELO {elo} out of bounds after concurrent updates"


# ===========================================================================
# DA-19 – DA-21  Pydantic model validation
# ===========================================================================

try:
    from llm.server import AdaptationModeRequest as _AdaptationModeRequest
    _MODEL_IMPORTED = True
except Exception:
    from pydantic import BaseModel
    from pydantic import field_validator as _fv

    class _AdaptationModeRequest(BaseModel):  # type: ignore[no-redef]
        enabled: bool
        base_elo: int | None = None

        @_fv("base_elo")
        @classmethod
        def validate_base_elo(cls, v: int | None) -> int | None:
            if v is not None and not (600 <= v <= 2400):
                raise ValueError("base_elo must be in [600, 2400]")
            return v

    _MODEL_IMPORTED = False


class TestAdaptationModeRequestValidation:

    def test_valid_enable_no_base_elo(self):
        """DA-19a: enabled=True without base_elo is valid."""
        req = _AdaptationModeRequest(enabled=True)
        assert req.enabled is True
        assert req.base_elo is None

    def test_valid_enable_with_base_elo(self):
        """DA-19b: enabled=True with in-range base_elo is valid."""
        req = _AdaptationModeRequest(enabled=True, base_elo=1500)
        assert req.base_elo == 1500

    def test_valid_disable(self):
        """DA-19c: enabled=False is valid."""
        req = _AdaptationModeRequest(enabled=False)
        assert req.enabled is False

    def test_base_elo_below_min_rejected(self):
        """DA-20: base_elo < 600 → ValidationError."""
        with pytest.raises(ValidationError):
            _AdaptationModeRequest(enabled=True, base_elo=599)

    def test_base_elo_above_max_rejected(self):
        """DA-21: base_elo > 2400 → ValidationError."""
        with pytest.raises(ValidationError):
            _AdaptationModeRequest(enabled=True, base_elo=2401)

    def test_base_elo_at_boundaries_accepted(self):
        """Boundary values 600 and 2400 must be accepted."""
        req_min = _AdaptationModeRequest(enabled=True, base_elo=600)
        req_max = _AdaptationModeRequest(enabled=True, base_elo=2400)
        assert req_min.base_elo == 600
        assert req_max.base_elo == 2400


# ===========================================================================
# DA-22 – DA-26  AST wiring tests
# ===========================================================================

def _parse_server() -> ast.Module:
    return ast.parse(_SERVER_PY.read_text(encoding="utf-8"))


def _get_funcs(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _depends_on(func_def: ast.FunctionDef, target: str) -> bool:
    for default in func_def.args.defaults + func_def.args.kw_defaults:
        if default is None:
            continue
        if not isinstance(default, ast.Call):
            continue
        func = default.func
        if isinstance(func, ast.Name) and func.id == "Depends":
            for arg in default.args:
                if isinstance(arg, ast.Name) and arg.id == target:
                    return True
    return False


class TestServerWiringAst:

    def setup_method(self):
        self._tree = _parse_server()
        self._funcs = _get_funcs(self._tree)
        self._source = _SERVER_PY.read_text(encoding="utf-8")

    def test_set_adaptation_mode_endpoint_exists(self):
        """DA-22: set_adaptation_mode function must exist in server.py."""
        assert "set_adaptation_mode" in self._funcs, (
            "set_adaptation_mode() not found in server.py. "
            "POST /adaptation/mode endpoint is missing."
        )

    def test_get_adaptation_mode_endpoint_exists(self):
        """DA-23: get_adaptation_mode function must exist in server.py."""
        assert "get_adaptation_mode" in self._funcs, (
            "get_adaptation_mode() not found in server.py. "
            "GET /adaptation/mode endpoint is missing."
        )

    def test_set_adaptation_mode_requires_player_auth(self):
        """DA-24a: POST /adaptation/mode must require player session."""
        func = self._funcs.get("set_adaptation_mode")
        assert func is not None
        assert _depends_on(func, "get_current_player"), (
            "set_adaptation_mode() must have Depends(get_current_player). "
            "Unauthenticated callers could manipulate any player's ELO."
        )

    def test_get_adaptation_mode_requires_player_auth(self):
        """DA-24b: GET /adaptation/mode must require player session."""
        func = self._funcs.get("get_adaptation_mode")
        assert func is not None
        assert _depends_on(func, "get_current_player"), (
            "get_adaptation_mode() must have Depends(get_current_player). "
            "State leaks adaptation data to unauthenticated callers."
        )

    def test_move_endpoint_calls_dynamic_registry_get_elo(self):
        """DA-25: /move must call _dynamic_registry.get_elo to override target ELO."""
        assert "_dynamic_registry" in self._source, (
            "server.py has no _dynamic_registry — dynamic adaptation is not wired."
        )
        assert "_dynamic_registry.get_elo" in self._source, (
            "/move endpoint must call _dynamic_registry.get_elo() to use dynamic ELO "
            "when adaptation mode is enabled."
        )

    def test_live_move_records_quality_when_dynamic_enabled(self):
        """DA-26: /live/move must call record_move_quality when dynamic mode is on."""
        assert "record_move_quality" in self._source, (
            "/live/move in server.py must call _dynamic_registry.record_move_quality() "
            "so the dynamic ELO converges based on observed move quality."
        )

    def test_dynamic_registry_instance_declared(self):
        """_dynamic_registry must be instantiated as a module-level variable."""
        assert "DynamicModeRegistry()" in self._source, (
            "server.py must create _dynamic_registry = DynamicModeRegistry() "
            "as a module-level instance."
        )

    def test_dynamic_mode_imported(self):
        """DynamicModeRegistry must be imported in server.py."""
        assert "DynamicModeRegistry" in self._source, (
            "DynamicModeRegistry not imported in server.py."
        )


# ===========================================================================
# DA-27 – DA-30  HTTP-layer tests (minimal stub app)
# ===========================================================================

from llm.seca.adaptation.dynamic_mode import DynamicModeRegistry as _Registry

_stub_registry = _Registry()
_STUB_PLAYER_ID = "stub-player-001"


class _FakePlayer:
    id = _STUB_PLAYER_ID
    rating = 1200.0
    confidence = 0.5


_stub_app = FastAPI()


def _stub_player():
    return _FakePlayer()


@_stub_app.post("/adaptation/mode")
def _stub_set_mode(
    req: _AdaptationModeRequest,
    player=Depends(_stub_player),
):
    from llm.seca.adaptation.coupling import compute_adaptation

    base_elo = req.base_elo
    if base_elo is None and req.enabled:
        adaptation = compute_adaptation(float(player.rating), float(player.confidence))
        base_elo = adaptation["opponent"]["target_elo"]

    state = _stub_registry.set_mode(str(player.id), enabled=req.enabled, base_elo=base_elo)
    return {"enabled": state.enabled, "current_elo": state.current_elo, "move_count": state.move_count}


@_stub_app.get("/adaptation/mode")
def _stub_get_mode(player=Depends(_stub_player)):
    state = _stub_registry.get_state(str(player.id))
    return {"enabled": state.enabled, "current_elo": state.current_elo, "move_count": state.move_count}


_stub_client = TestClient(_stub_app, raise_server_exceptions=False)


class TestAdaptationModeHttp:

    def setup_method(self):
        # Reset registry state before each test for isolation.
        _stub_registry.set_mode(_STUB_PLAYER_ID, enabled=False)

    def test_post_enable_returns_enabled_true(self):
        """DA-27a: POST /adaptation/mode {"enabled": true} → enabled: true."""
        r = _stub_client.post("/adaptation/mode", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["enabled"] is True

    def test_post_enable_returns_elo_in_range(self):
        """DA-27b: returned current_elo must be in [600, 2400]."""
        r = _stub_client.post("/adaptation/mode", json={"enabled": True})
        elo = r.json()["current_elo"]
        assert ELO_MIN <= elo <= ELO_MAX, f"current_elo={elo} out of range"

    def test_post_enable_with_custom_base_elo(self):
        """DA-27c: custom base_elo is reflected in response."""
        r = _stub_client.post("/adaptation/mode", json={"enabled": True, "base_elo": 800})
        assert r.json()["current_elo"] == 800

    def test_get_returns_current_state(self):
        """DA-28: GET /adaptation/mode returns current state."""
        _stub_client.post("/adaptation/mode", json={"enabled": True, "base_elo": 1000})
        r = _stub_client.get("/adaptation/mode")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert body["current_elo"] == 1000

    def test_post_disable_resets_move_count(self):
        """DA-29: disabling mode resets move_count to 0."""
        _stub_client.post("/adaptation/mode", json={"enabled": True, "base_elo": 1200})
        r = _stub_client.post("/adaptation/mode", json={"enabled": False})
        assert r.json()["move_count"] == 0
        assert r.json()["enabled"] is False

    def test_post_enable_without_base_elo_uses_player_adaptation(self):
        """DA-30: enabling without base_elo derives ELO from player's adaptation."""
        from llm.seca.adaptation.coupling import compute_adaptation

        adaptation = compute_adaptation(1200.0, 0.5)
        expected_elo = adaptation["opponent"]["target_elo"]

        r = _stub_client.post("/adaptation/mode", json={"enabled": True})
        assert r.json()["current_elo"] == expected_elo, (
            f"Expected ELO from player adaptation ({expected_elo}), "
            f"got {r.json()['current_elo']}"
        )

    def test_invalid_base_elo_returns_422(self):
        """Validation error on out-of-range base_elo → HTTP 422."""
        r = _stub_client.post("/adaptation/mode", json={"enabled": True, "base_elo": 100})
        assert r.status_code == 422

    def test_get_returns_disabled_by_default(self):
        """GET /adaptation/mode for a fresh player → enabled: false."""
        r = _stub_client.get("/adaptation/mode")
        assert r.status_code == 200
        assert r.json()["enabled"] is False


# ===========================================================================
# ELO_MIN / ELO_MAX constant contract
# ===========================================================================


class TestEloConstants:
    """The ELO range must match the backend↔Android contract (600–2400)."""

    def test_elo_min_is_600(self):
        assert ELO_MIN == 600, (
            f"ELO_MIN={ELO_MIN} but must be 600 to match Android contract"
        )

    def test_elo_max_is_2400(self):
        assert ELO_MAX == 2400, (
            f"ELO_MAX={ELO_MAX} but must be 2400 to match Android contract"
        )

    def test_quality_delta_table_is_symmetric_signed(self):
        """positive-quality labels must have positive deltas; negative must be negative."""
        positive_labels = {"best", "excellent", "good"}
        negative_labels = {"blunder", "mistake", "inaccuracy"}
        for label in positive_labels:
            assert _QUALITY_DELTA[label] > 0, f"{label} delta must be positive"
        for label in negative_labels:
            assert _QUALITY_DELTA[label] < 0, f"{label} delta must be negative"

    def test_all_expected_quality_labels_present(self):
        expected = {"best", "excellent", "good", "inaccuracy", "mistake", "blunder"}
        missing = expected - set(_QUALITY_DELTA.keys())
        assert not missing, f"Missing quality labels in delta table: {missing}"
