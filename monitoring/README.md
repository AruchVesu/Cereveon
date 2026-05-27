# Monitoring

This folder holds the production observability config. Two pieces:

| File | Role |
|---|---|
| `alloy.alloy` | Grafana Alloy config — runs on the Hetzner host, ships container stdout to Grafana Cloud Loki and scrapes `api:8000/metrics` to Grafana Cloud Prometheus. Loaded by the alloy container in `docker-compose.yml`. |
| `dashboards/llm_hardware.json` | Grafana dashboard JSON. Covers LLM latency / tokens / cost / errors and host CPU / memory / disk / load. |

## Importing the dashboard

Grafana Cloud (or any Grafana ≥ v10) → **Dashboards → New → Import**:

1. Upload `dashboards/llm_hardware.json`.
2. When prompted, bind the two template variables:
   - `datasource_prom` → your Grafana Cloud Prometheus datasource (the
     same one Alloy is pushing to via `prometheus.remote_write`).
   - `datasource_loki` → your Grafana Cloud Loki datasource (the same
     one Alloy is pushing to via `loki.write`).
3. Save.

First data lands within ~2 minutes:

- Prometheus panels (latency, tokens, cost, errors, hardware) — at the
  next 30 s scrape (`alloy.alloy::scrape_interval`).
- Cost-per-match LogQL panel — needs at least one `event="llm_call"`
  log line carrying a non-empty `game_id`. That requires a request
  through `/game/{id}/checkpoint` or `/game/finish` that triggers an
  LLM call. Free-form `/chat` calls without a game scope are
  intentionally excluded — those have `game_id: null`.

## Updating the dashboard

Edit `dashboards/llm_hardware.json` directly. The schema is validated
on every CI run by `llm/tests/test_grafana_dashboard_json.py` — adding
a panel without a `datasource` or a `targets[*].expr` fails the build.

If you tweak the dashboard live in Grafana and want to commit the
changes, export the JSON via **Dashboard settings → JSON Model → Save
to file** and overwrite `dashboards/llm_hardware.json`. Strip any
`"id": <int>` at the top level (Grafana adds it; importable JSON does
not need it).

## Adding new metrics

All metric definitions live in `llm/observability.py`. Add a new
Prometheus metric there, instrument the call site, then add a panel
to `dashboards/llm_hardware.json` that queries it. The `/metrics`
endpoint exposes the new series automatically — no Alloy config
change is needed.
