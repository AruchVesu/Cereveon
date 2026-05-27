"""Tests for the psutil-backed hardware gauges (CPU/memory/disk/load).

Pinned invariants
-----------------
HW_MET_01  Each gauge is registered on the global Prometheus registry.
HW_MET_02  Each gauge callback returns a finite non-negative float.
HW_MET_03  The CPU gauge survives a psutil failure and returns 0 rather
           than raising (a flaky psutil call must not 500 the scrape).
HW_MET_04  The disk gauge picks the configured ``CHESSCOACH_DISK_METRIC_PATH``
           when set.
HW_MET_05  The load-avg gauge returns 0 on platforms without getloadavg
           (Windows dev) rather than raising.
"""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest
from prometheus_client import REGISTRY


# ---------------------------------------------------------------------------
# HW_MET_01  Gauges registered
# ---------------------------------------------------------------------------


def test_hw_met_01_gauges_registered():
    from prometheus_client.exposition import generate_latest

    body = generate_latest(REGISTRY).decode()
    expected = [
        "chesscoach_cpu_percent",
        "chesscoach_memory_percent",
        "chesscoach_memory_used_bytes",
        "chesscoach_disk_percent",
        "chesscoach_load_avg_1m",
    ]
    for name in expected:
        assert name in body, f"missing hardware gauge {name!r}"


# ---------------------------------------------------------------------------
# HW_MET_02  Callbacks return real numbers
# ---------------------------------------------------------------------------


def _gauge_value_from_registry(name: str) -> float | None:
    """Read a gauge sample value directly from the registry."""
    return REGISTRY.get_sample_value(name)


def test_hw_met_02_each_gauge_returns_a_finite_non_negative_float():
    """Each gauge callback must return a real number; NaN / inf / negative
    would render as garbage in Grafana and break alert rules."""
    names = [
        "chesscoach_cpu_percent",
        "chesscoach_memory_percent",
        "chesscoach_memory_used_bytes",
        "chesscoach_disk_percent",
        "chesscoach_load_avg_1m",
    ]
    for name in names:
        value = _gauge_value_from_registry(name)
        assert value is not None, f"{name} did not produce a sample"
        assert isinstance(value, float)
        assert math.isfinite(value), f"{name} produced non-finite value {value}"
        assert value >= 0.0, f"{name} produced negative value {value}"


# ---------------------------------------------------------------------------
# HW_MET_03  Failure isolation
# ---------------------------------------------------------------------------


def test_hw_met_03_cpu_callback_swallows_psutil_failure():
    """A failed psutil call must NOT propagate from a scrape callback.
    Returns 0.0 and logs.  /metrics must always stay green even when the
    sampling library throws."""
    from llm import observability

    with patch.object(
        observability.psutil, "cpu_percent", side_effect=RuntimeError("boom")
    ):
        assert observability._safe_cpu_percent() == 0.0


def test_hw_met_03b_memory_callback_swallows_psutil_failure():
    from llm import observability

    with patch.object(
        observability.psutil, "virtual_memory", side_effect=RuntimeError("boom")
    ):
        assert observability._safe_memory_percent() == 0.0
        assert observability._safe_memory_used_bytes() == 0.0


def test_hw_met_03c_disk_callback_swallows_psutil_failure():
    from llm import observability

    with patch.object(
        observability.psutil, "disk_usage", side_effect=RuntimeError("boom")
    ):
        assert observability._safe_disk_percent() == 0.0


# ---------------------------------------------------------------------------
# HW_MET_04  Configured disk path
# ---------------------------------------------------------------------------


def test_hw_met_04_disk_path_can_be_overridden(monkeypatch):
    """An operator who needs to monitor a different volume (e.g. a mounted
    data disk on Hetzner) can set CHESSCOACH_DISK_METRIC_PATH at process
    start.  The variable is read at module import; this test verifies the
    helper reads from the live constant by re-pointing it and asserting
    psutil.disk_usage is called with the new path."""
    from llm import observability

    captured: list[str] = []

    class _FakeUsage:
        percent = 12.5

    def _spy(path: str) -> _FakeUsage:
        captured.append(path)
        return _FakeUsage()

    monkeypatch.setattr(observability, "_HARDWARE_DISK_PATH", "/tmp")
    with patch.object(observability.psutil, "disk_usage", side_effect=_spy):
        value = observability._safe_disk_percent()
    assert value == pytest.approx(12.5)
    assert captured == ["/tmp"]


# ---------------------------------------------------------------------------
# HW_MET_05  Load-avg fallback
# ---------------------------------------------------------------------------


def test_hw_met_05_load_avg_handles_missing_getloadavg(monkeypatch):
    """psutil.getloadavg is unix-only.  On Windows / when the call is
    unavailable, the helper must return 0 rather than letting the scrape
    fail."""
    from llm import observability

    # Force the hasattr branch to fail.
    monkeypatch.delattr(observability.psutil, "getloadavg", raising=False)
    assert observability._safe_load_avg_1m() == 0.0


def test_hw_met_05b_load_avg_handles_os_error(monkeypatch):
    """When getloadavg exists but raises (some psutil versions on
    sandboxed Linux containers), the helper still returns 0."""
    from llm import observability

    def _raising_loadavg() -> tuple[float, float, float]:
        raise OSError("no /proc/loadavg in this sandbox")

    monkeypatch.setattr(observability.psutil, "getloadavg", _raising_loadavg)
    assert observability._safe_load_avg_1m() == 0.0
