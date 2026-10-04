# Claude Code repository guidance

Read [AGENTS.md](AGENTS.md) before making changes. It is the shared source for architecture,
setup, verification, approval boundaries and generation-quality rules.

The active UI is Next.js in frontend/, connected to src/maf_test_harness/ui/api.py.
Start both services with `uv run python scripts/start_ui.py`. Follow the commands in AGENTS.md;
the retired Streamlit entry point is not part of the current application.

Do not delegate work to other agents unless the user requests it. For generation evaluation,
read [docs/GENERATION_BENCHMARK.md](docs/GENERATION_BENCHMARK.md). Reference calibration is
separate from evaluating generated candidates.
