#!/usr/bin/env bash
# First-time bootstrapping script for a ChessCoach-AI Hetzner VPS.
#
# Run once as the 'deploy' user (member of the 'docker' group) from /opt/chesscoach
# after cloning or rsync-ing the repo to the server.
#
# What this script does:
#   1. Validates prerequisites (Docker Compose plugin, curl, python3)
#   2. Generates .env.prod from .env.prod.example if it does not yet exist
#   3. Pulls all GHCR images
#   4. Starts the production stack (db, redis, api, caddy)
#   5. Waits for the API health check and runs smoke tests
#
# LLM provider: the api container talks to DeepSeek's managed API directly
# (https://api.deepseek.com), so there is no local LLM container to start.
# COACH_DEEPSEEK_API_KEY must be set in .env.prod for coaching to work;
# without it, /chat falls back to a deterministic template.
#
# For subsequent deploys CI handles everything automatically via the
# "Deploy to Hetzner" job in .github/workflows/fly-deploy.yml.
#
# Usage:
#   cd /opt/chesscoach
#   ./scripts/hetzner_setup.sh

set -euo pipefail

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.prod"
ENV_TEMPLATE=".env.prod.example"
API_URL="http://127.0.0.1:8000"
SMOKE_SCRIPT="scripts/smoke_test.sh"

_bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
_green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
_yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
_red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
_die()    { _red "ERROR: $*"; exit 1; }

# ── Working directory ──────────────────────────────────────────────────────────
[ -f "$COMPOSE_FILE" ] || _die "Run this script from /opt/chesscoach (docker-compose.prod.yml not found)"

# ── Prerequisites ──────────────────────────────────────────────────────────────
_bold "==> 1/6  Checking prerequisites"
command -v docker  >/dev/null 2>&1 || _die "docker not found. Install Docker Engine: https://docs.docker.com/engine/install/"
command -v curl    >/dev/null 2>&1 || _die "curl not found (apt install curl)."
command -v python3 >/dev/null 2>&1 || _die "python3 not found (apt install python3)."
docker compose version >/dev/null 2>&1 || _die "Docker Compose plugin not found (apt install docker-compose-plugin)."
_green "    Prerequisites OK."
echo ""

# ── .env.prod ──────────────────────────────────────────────────────────────────
_bold "==> 2/6  Preparing $ENV_FILE"
if [ -f "$ENV_FILE" ]; then
    _yellow "    $ENV_FILE already exists — skipping generation."
else
    [ -f "$ENV_TEMPLATE" ] || _die "$ENV_TEMPLATE not found."
    cp "$ENV_TEMPLATE" "$ENV_FILE"
    # Inject a fresh SECRET_KEY so operators only need to fill the remaining blanks.
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s|SECRET_KEY=CHANGE_ME|SECRET_KEY=${SECRET_KEY}|" "$ENV_FILE"
    _yellow "    $ENV_FILE created with a generated SECRET_KEY."
    _yellow "    Open $ENV_FILE and set the following before continuing:"
    _yellow "      SECA_API_KEY              — must match COACH_API_KEY in the Android APK"
    _yellow "      COACH_DEEPSEEK_API_KEY    — DeepSeek API key (https://platform.deepseek.com)."
    _yellow "                                  Without it /chat falls back to deterministic templates."
    _yellow "      POSTGRES_PASSWORD / DATABASE_URL — use the same password in both"
    _yellow "      DOMAIN                    — your public domain for Caddy TLS (e.g. api.example.com)"
    _yellow "      GHCR_IMAGE                — full GHCR image ref (e.g. ghcr.io/owner/cereveon-llm-api:latest)"
    echo ""
    read -r -p "  Press Enter after editing $ENV_FILE, or Ctrl-C to abort: "
fi
echo ""

# Validate: no CHANGE_ME placeholders remain for critical keys.
# shellcheck source=/dev/null
set -o allexport
source "$ENV_FILE"
set +o allexport

[[ "${SECA_API_KEY:-}"      != *CHANGE_ME* ]] || _die "SECA_API_KEY is still a placeholder — edit $ENV_FILE."
[[ "${SECRET_KEY:-}"        != *CHANGE_ME* ]] || _die "SECRET_KEY is still a placeholder — edit $ENV_FILE."
[[ "${POSTGRES_PASSWORD:-}" != *CHANGE_ME* ]] || _die "POSTGRES_PASSWORD is still a placeholder — edit $ENV_FILE."
[[ -n "${DOMAIN:-}"         ]]                || _die "DOMAIN is not set in $ENV_FILE."
[[ -n "${GHCR_IMAGE:-}"     ]]                || _die "GHCR_IMAGE is not set in $ENV_FILE."
# COACH_DEEPSEEK_API_KEY is optional — coaching degrades to deterministic
# templates without it, but the api still serves.  Warn rather than die.
if [[ -z "${COACH_DEEPSEEK_API_KEY:-}" || "${COACH_DEEPSEEK_API_KEY}" == *CHANGE_ME* ]]; then
    _yellow "    Warning: COACH_DEEPSEEK_API_KEY is unset/placeholder — /chat will fall"
    _yellow "    back to deterministic templates.  Set the key in $ENV_FILE for live LLM."
fi
_green "    $ENV_FILE validated."
echo ""

# ── Pull images ────────────────────────────────────────────────────────────────
_bold "==> 3/5  Pulling Docker images from GHCR"
DOMAIN="${DOMAIN}" GHCR_IMAGE="${GHCR_IMAGE}" \
    docker compose -f "$COMPOSE_FILE" pull
_green "    Images pulled."
echo ""

# ── Start full stack ───────────────────────────────────────────────────────────
_bold "==> 4/5  Starting full production stack"
DOMAIN="${DOMAIN}" GHCR_IMAGE="${GHCR_IMAGE}" \
    docker compose -f "$COMPOSE_FILE" up -d
_green "    Stack started."
echo ""

# ── API health check ───────────────────────────────────────────────────────────
_bold "==> 5/5  Waiting for API health check"
for i in $(seq 1 24); do
    if curl -sf "${API_URL}/health" >/dev/null 2>&1; then
        _green "    API is healthy."
        break
    fi
    [ "$i" -lt 24 ] || _die "API did not become healthy after 120 s. Check: docker compose -f $COMPOSE_FILE logs api"
    echo "    Waiting... ($i/24)"
    sleep 5
done
echo ""

# ── Smoke tests ────────────────────────────────────────────────────────────────
if [ -f "$SMOKE_SCRIPT" ]; then
    _bold "Running smoke tests..."
    bash "$SMOKE_SCRIPT" "${API_URL}" "${SECA_API_KEY}"
    echo ""
else
    _yellow "Smoke script not found at $SMOKE_SCRIPT — skipping."
fi

# ── Done ───────────────────────────────────────────────────────────────────────
_bold "========================================================"
_green "Bootstrap complete!"
_green "Public endpoint: https://${DOMAIN}"
echo ""
_yellow "Next steps:"
_yellow "  1. Confirm Caddy provisioned a TLS certificate: docker compose -f $COMPOSE_FILE logs caddy"
_yellow "  2. Register the first player via POST /auth/register"
_yellow "  3. Set HETZNER_HOST and HETZNER_SSH_KEY in GitHub → Settings → Secrets"
_yellow "     so that CI deploys automatically on every push to main."
_bold "========================================================"
