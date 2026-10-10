#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
command -v npm >/dev/null || { echo "Install Node.js 24 LTS first: https://nodejs.org/"; exit 1; }
node_version=$(node --version 2>/dev/null) || { echo "Node.js not found. Install Node.js 24 LTS: https://nodejs.org/"; exit 1; }
if ! echo "$node_version" | grep -q '^v24\.'; then
    echo "Node.js 24 is required. Found $node_version."
    echo "Installed Node.js versions:"
    which -a node
    echo ""
    echo "Fix: Install Node.js 24 LTS (https://nodejs.org/), or with nvm run: nvm install 24 && nvm use 24; then remove older Node entries from PATH and open a new terminal."
    exit 1
fi
if [ ! -f .env ]; then (umask 077; cp .env.example .env); echo "Created .env. Configure your gateway there before starting a run."; fi
qe_status=0
uv run --no-project --python 3.12 python scripts/start_ui.py --check-only "$@" || qe_status=$?
if [ "$qe_status" -eq 10 ]; then exit 0; fi
if [ "$qe_status" -ne 0 ]; then exit "$qe_status"; fi
uv sync --python 3.12 --extra dev --extra e2e
uv run python scripts/start_ui.py --prepare "$@"
exec uv run python scripts/start_ui.py --production "$@"
