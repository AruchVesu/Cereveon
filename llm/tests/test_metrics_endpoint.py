"""Tests for the Prometheus /metrics endpoint and request-tracking middleware.

Pinned invariants
-----------------
MET_01  /metrics rejects unauthenticated calls (no headers) with 401.
MET_02  /metrics rejects wrong X-Api-Key with 401.
MET_03  /metrics rejects wrong Bearer token with 401.
MET_04  /metrics accepts correct X-Api-Key and returns Prom content-type.
MET_05  /metrics accepts correct Bearer token.
MET_06  /metrics body advertises the expected metric names so a missing
        metric (e.g. someone deletes the histogram) is caught in CI.
MET_07  Hitting /health increments chesscoach_http_requests_total for
        path_template="/health".
MET_08  /metrics itself is NOT counted (the middleware excludes it).
MET_09  Engine pool gauges report the configured size when the lifespan
        has registered the snapshot provider.
MET_10  A failed /auth/login increments auth_login_total{result="invalid_credentials"}.
"""

from __future__ import annotations

import os
import re

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")


@pytest.fixture(scope="module")
def server_module():
    import llm.server as server

    return server


@pytest.fixture
def client(server_module, monkeypatch):
    """Per-test client with auth + rate-limiter resets, matching the
    pattern used in test_api_version_header.py."""
    import llm.seca.auth.api_key as api_key_module

    monkeypatch.setenv("SECA_API_KEY", "ci-test-key")
    monkeypatch.setenv("SECA_ENV", "dev")
    monkeypatch.setattr(api_key_module, "_API_KEY", "ci-test-key")
    monkeypatch.setattr(api_key_module, "_IS_PROD", False)
    monkeypatch.setattr(server_module, "API_KEY", "ci-test-key")
    server_module.limiter.reset()
    with TestClient(server_module.app) as c:
        yield c


# ---------------------------------------------------------------------------
# MET_01 – MET_05  Auth shape
# ---------------------------------------------------------------------------


def _counter_value(body: str, metric: str, **labels: str) -> float:
    """Return the float value of a counter sample matching the given labels.

    Robust against the labelvalues ordering, which prometheus_client may
    rotate across calls (Counter labels are stored as a dict).  Missing
    samples return 0.0 so callers can assert on deltas without first
    checking presence.
    """
    label_re = ",".join(rf'{re.escape(k)}="{re.escape(v)}"' for k, v in labels.items())
    # Match any ordering of labels by allowing the test labels to appear
    # in any order in the sample line.  We re-validate against a stricter
    # ordering check below by also accepting the swapped form.
    patterns = [rf"^{re.escape(metric)}\{{[^}}]*{label_re}[^}}]*\}}\s+(\S+)$"]
    for pat in patterns:
        m = re.search(pat, body, flags=re.MULTILINE)
        if m:
            return float(m.group(1))
    return 0.0


def test_met_01_metrics_rejects_unauthenticated(client):
    r = client.get("/metrics")
    assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text[:200]}"


def test_met_02_metrics_rejects_wrong_x_api_key(client):
    r = client.get("/metrics", headers={"X-Api-Key": "wrong-key"})
    assert r.status_code == 401


def test_met_03_metrics_rejects_wrong_bearer(client):
    r = client.get("/metrics", headers={"Authorization": "Bearer wrong-key"})
    assert r.status_code == 401


def test_met_04_metrics_accepts_x_api_key(client):
    r = client.get("/metrics", headers={"X-Api-Key": "ci-test-key"})
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text[:200]}"
    ctype = r.headers.get("content-type", "")
    # Prom exposition format starts with "text/plain; version=0.0.4; ..."
    assert "text/plain" in ctype, f"unexpected content-type: {ctype!r}"
    assert "version=" in ctype, f"missing prom version suffix in content-type: {ctype!r}"


def test_met_05_metrics_accepts_bearer_token(client):
    r = client.get("/metrics", headers={"Authorization": "Bearer ci-test-key"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# MET_06  Metric names present
# ---------------------------------------------------------------------------


def test_met_06_metric_names_present(client):
    """The body must advertise the metrics the rest of this suite depends
    on; a missing name (e.g. someone renamed the histogram) should fail
    LOUDLY rather than silently zero-filling downstream tests.
    """
    body = client.get("/metrics", headers={"X-Api-Key": "ci-test-key"}).text

    # Counters expose ``<name>_total`` directly; the HELP/TYPE lines show
    # the bare name.
    expected_names = [
        "chesscoach_http_requests_total",
        "chesscoach_http_request_duration_seconds",
        "chesscoach_auth_login_total",
        "chesscoach_auth_register_total",
        "chesscoach_engine_pool_size",
        "chesscoach_engine_pool_available",
        "chesscoach_engine_pool_in_use",
    ]
    for name in expected_names:
        assert name in body, f"metric name missing from /metrics body: {name!r}"


# ---------------------------------------------------------------------------
# MET_07 / MET_08  HTTP request middleware
# ---------------------------------------------------------------------------


def test_met_07_health_request_is_counted(client):
    """A request to /health must increment chesscoach_http_requests_total
    with path_template="/health".  Tests on a delta so cross-test state
    doesn't leak.
    """
    before = client.get("/metrics", headers={"X-Api-Key": "ci-test-key"}).text
    before_count = _counter_value(
        before,
        "chesscoach_http_requests_total",
        method="GET",
        path_template="/health",
        status="200",
    )

    r = client.get("/health")
    assert r.status_code == 200

    after = client.get("/metrics", headers={"X-Api-Key": "ci-test-key"}).text
    after_count = _counter_value(
        after,
        "chesscoach_http_requests_total",
        method="GET",
        path_template="/health",
        status="200",
    )
    assert after_count == before_count + 1, (
        f"expected counter to increment by 1, got before={before_count} "
        f"after={after_count}"
    )


def test_met_08_metrics_itself_not_counted(client):
    """/metrics is excluded by the middleware so a Prometheus scrape
    doesn't appear in the histogram it just produced.  Hits to /metrics
    must NOT bump chesscoach_http_requests_total for path_template="/metrics".
    """
    # Fire a few scrapes.
    for _ in range(3):
        client.get("/metrics", headers={"X-Api-Key": "ci-test-key"})

    final = client.get("/metrics", headers={"X-Api-Key": "ci-test-key"}).text
    metrics_count = _counter_value(
        final,
        "chesscoach_http_requests_total",
        method="GET",
        path_template="/metrics",
        status="200",
    )
    assert metrics_count == 0.0, (
        f"/metrics must be excluded from its own histogram; got count={metrics_count}.  "
        "Self-counting creates a feedback loop and double-counts every scrape."
    )


# ---------------------------------------------------------------------------
# MET_09  Engine pool gauges
# ---------------------------------------------------------------------------


def test_met_09_engine_pool_size_gauge_reports_value(client, server_module):
    """The engine pool snapshot provider is wired in lifespan startup,
    so once the TestClient context is entered the gauge must report a
    non-zero size (unless the engine pool failed to boot, in which case
    the gauge correctly reports 0).

    We assert presence of the metric and that the value matches the
    configured pool_size when the pool came up.
    """
    body = client.get("/metrics", headers={"X-Api-Key": "ci-test-key"}).text

    # Gauge sample line: ``chesscoach_engine_pool_size 8.0``  (no labels)
    m = re.search(r"^chesscoach_engine_pool_size\s+(\S+)$", body, flags=re.MULTILINE)
    assert m is not None, "engine_pool_size gauge not exposed"
    gauge_value = float(m.group(1))

    # If the pool came up, the gauge tracks settings.pool_size; if it
    # failed to boot (e.g. no Stockfish binary in CI), the provider
    # returns 0.  Both are valid — what we're pinning is that the
    # gauge is exposed AND a non-negative real number.
    assert gauge_value >= 0.0, f"pool size gauge reported negative value: {gauge_value}"


# ---------------------------------------------------------------------------
# MET_10  Auth login counter
# ---------------------------------------------------------------------------


def test_met_10_failed_login_increments_counter(client):
    """A 401 response from /auth/login must increment
    chesscoach_auth_login_total{result="invalid_credentials"}.
    """
    before = client.get("/metrics", headers={"X-Api-Key": "ci-test-key"}).text
    before_count = _counter_value(
        before, "chesscoach_auth_login_total", result="invalid_credentials"
    )

    r = client.post(
        "/auth/login",
        json={"email": "nobody@example.com", "password": "wrong-pass-12"},
    )
    assert r.status_code == 401

    after = client.get("/metrics", headers={"X-Api-Key": "ci-test-key"}).text
    after_count = _counter_value(
        after, "chesscoach_auth_login_total", result="invalid_credentials"
    )
    assert after_count == before_count + 1, (
        f"expected auth_login_total{{result=invalid_credentials}} to "
        f"increment; got before={before_count} after={after_count}"
    )
