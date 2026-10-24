#!/usr/bin/env bash
# Build the UI (if needed) and serve API + UI on http://localhost:8000
# Usage: LINEAGE_DATA_DIR=/path/to/exports ./run.sh
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r backend/requirements.txt; }
[ -d frontend/dist ] || (cd frontend && npm install --no-audit --no-fund && npm run build)
cd backend && exec ../.venv/bin/uvicorn lineage.api:app --host 0.0.0.0 --port "${PORT:-8000}"
