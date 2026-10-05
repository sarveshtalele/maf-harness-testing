#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
command -v npm >/dev/null || { echo "Install Node.js 24 LTS first: https://nodejs.org/"; exit 1; }
if [ ! -f .env ]; then cp .env.example .env; chmod 600 .env; echo "Created .env. Configure your gateway there before starting a run."; fi
qe_status=0
uv run --no-project --python 3.12 python scripts/start_ui.py --check-only "$@" || qe_status=$?
if [ "$qe_status" -eq 10 ]; then exit 0; fi
if [ "$qe_status" -ne 0 ]; then exit "$qe_status"; fi
uv sync --python 3.12 --extra dev --extra e2e
uv run python scripts/start_ui.py --prepare "$@"
exec uv run python scripts/start_ui.py --production "$@"
