# Repository guidance for GitHub Copilot

Read and follow [AGENTS.md](../AGENTS.md), the shared repository guidance for architecture,
setup, verification and generation-quality rules. Stay within the user's scope; do not delegate
work to other agents unless requested.

This is a local Next.js UI (frontend/) with a Python / Microsoft Agent Framework backend
(src/maf_test_harness/). UI and CLI share the checkpointed workflow. The API credential stays
server-side. Models propose artifacts; deterministic host validation, human approvals and native
runners govern execution and verdicts. Preserve these boundaries.

Use `uv run python scripts/start_ui.py` to start both services and the scoped checks in AGENTS.md.
Preserve genuine assertions and classify test, environment and application failures separately.
Never report collection, mocked browser journeys or reference calibration as live model quality.
See [the benchmark guide](../docs/GENERATION_BENCHMARK.md).
