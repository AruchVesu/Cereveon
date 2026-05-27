"""Schema validation for the Grafana dashboard JSON shipped under
``monitoring/dashboards/``.

Catches regressions where someone adds a panel without a datasource
binding, breaks the templating section, or removes a query reference to
one of the metrics this dashboard depends on.

Pinned invariants
-----------------
DASH_01  ``monitoring/dashboards/llm_hardware.json`` exists and parses.
DASH_02  Required top-level fields are present (schemaVersion, title,
         uid, panels).
DASH_03  Every panel has a numeric ``id``, a ``type``, and a non-empty
         ``title``.
DASH_04  Every non-row panel has a ``datasource`` binding.
DASH_05  Every non-row panel has at least one ``targets[*].expr``.
DASH_06  The dashboard references every new metric the LLM + hardware
         observability surface advertises — a missing reference means
         the dashboard silently lost a panel.
DASH_07  Both templating variables (``datasource_prom`` and
         ``datasource_loki``) are present.
"""

from __future__ import annotations

import json
from pathlib import Path


_DASHBOARD_PATH = (
    Path(__file__).resolve().parents[2]
    / "monitoring"
    / "dashboards"
    / "llm_hardware.json"
)


def _load() -> dict:
    assert _DASHBOARD_PATH.exists(), (
        f"dashboard JSON not found at {_DASHBOARD_PATH}; "
        "renaming or relocating it without updating this test silently breaks "
        "the import-once workflow documented in monitoring/README.md."
    )
    return json.loads(_DASHBOARD_PATH.read_text(encoding="utf-8"))


def test_dash_01_parses_as_json():
    payload = _load()
    assert isinstance(payload, dict)


def test_dash_02_top_level_fields_present():
    payload = _load()
    for field in ("schemaVersion", "title", "uid", "panels"):
        assert field in payload, f"missing top-level field {field!r}"
    assert isinstance(payload["panels"], list) and payload["panels"], (
        "panels must be a non-empty list"
    )


def test_dash_03_every_panel_has_id_type_title():
    payload = _load()
    seen_ids: set[int] = set()
    for panel in payload["panels"]:
        assert isinstance(panel.get("id"), int), f"panel missing int id: {panel}"
        assert panel["id"] not in seen_ids, (
            f"duplicate panel id {panel['id']} — Grafana will drop the duplicate on import"
        )
        seen_ids.add(panel["id"])
        assert panel.get("type"), f"panel {panel['id']} missing type"
        assert panel.get("title"), f"panel {panel['id']} missing title"


def test_dash_04_every_non_row_panel_has_datasource():
    payload = _load()
    for panel in payload["panels"]:
        if panel["type"] == "row":
            continue
        ds = panel.get("datasource")
        assert ds, f"panel {panel['id']} ({panel['title']!r}) missing datasource"
        if isinstance(ds, dict):
            assert ds.get("type") in ("prometheus", "loki"), (
                f"panel {panel['id']} datasource.type must be prometheus or loki; "
                f"got {ds!r}"
            )


def test_dash_05_every_non_row_panel_has_an_expr():
    payload = _load()
    for panel in payload["panels"]:
        if panel["type"] == "row":
            continue
        targets = panel.get("targets") or []
        assert targets, f"panel {panel['id']} ({panel['title']!r}) has no targets"
        for target in targets:
            assert target.get("expr"), (
                f"panel {panel['id']} ({panel['title']!r}) target missing expr: {target!r}"
            )


def test_dash_06_dashboard_references_every_new_metric():
    """Every new observability series this PR adds must be referenced by
    at least one panel — otherwise the dashboard silently lost a row
    when someone edited it.  Catches deletions in code review."""
    payload = _load()
    body = json.dumps(payload)
    required_references = [
        # LLM
        "chesscoach_llm_request_duration_seconds",
        "chesscoach_llm_tokens_total",
        "chesscoach_llm_cost_usd_total",
        "chesscoach_llm_errors_total",
        # HTTP error filter view (no new counter)
        "chesscoach_http_requests_total",
        # Hardware
        "chesscoach_cpu_percent",
        "chesscoach_memory_percent",
        "chesscoach_memory_used_bytes",
        "chesscoach_disk_percent",
        "chesscoach_load_avg_1m",
        # LogQL event tag
        "llm_call",
    ]
    for name in required_references:
        assert name in body, (
            f"dashboard no longer references {name!r}; either re-add the "
            "panel or update this test if the metric was retired."
        )


def test_dash_07_both_datasource_variables_present():
    payload = _load()
    variables = (payload.get("templating") or {}).get("list") or []
    names = {v.get("name") for v in variables}
    assert "datasource_prom" in names, "templating var datasource_prom missing"
    assert "datasource_loki" in names, "templating var datasource_loki missing"
