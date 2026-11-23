#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Lucid Turnstile Solver - launch script
# Installs pinned deps (idempotent) and boots uvicorn with N workers.
# Used both as the Docker CMD and the systemd ExecStart.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")"

# Load .env if present and export into our environment.  systemd users should
# prefer EnvironmentFile= instead; this is a convenience for bare-metal runs.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-4}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo "lucid-solver: installing dependencies..."
pip install --no-cache-dir --quiet -r requirements.txt

echo "lucid-solver: starting uvicorn host=${HOST} port=${PORT} workers=${WORKERS}"
exec python -m uvicorn solver.api:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --workers "${WORKERS}" \
  --log-level "${LOG_LEVEL}" \
  --no-access-log
