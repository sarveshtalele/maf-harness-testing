# Agent guide (Copilot, Codex and other coding assistants)

Short entry point. Authoritative guidance: [CLAUDE.md](CLAUDE.md) (root rules and merge gate), the `CLAUDE.md` in each directory you edit, and [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) (setup, standards, review and how-to recipes). This file keeps only the rules that apply to agents.

## What the project is

Agentic QE Platform: a Next.js UI and a loopback Python API over a checkpointed Microsoft Agent Framework workflow. It turns a public GitHub repository (brownfield) or specs (greenfield) into reviewed unit and integration tests, runs them with approval, and writes a QA report. It ships as a local launcher and as an Electron app in `desktop/`. Backend code is in `src/maf_test_harness/`; agent skills are in `skills/`.

## Must-follow rules

- Every change must work on Windows, macOS and Linux: `pathlib`, `encoding="utf-8"`, argument lists, never `shell=True` ([guide 5.2](docs/DEVELOPER_GUIDE.md#52-cross-os-rules)).
- Never commit secrets, `.env`, run evidence or `agent-framework.pdf`, and never print API keys ([guide 5.3](docs/DEVELOPER_GUIDE.md#53-security-rules)).
- Never relax repository dependency pins, and never replace missing application dependencies with fake tests ([guide 5.4](docs/DEVELOPER_GUIDE.md#54-dependency-pins)).
- Never bypass the skill lock (`skills/SKILLS.lock.json`), plugin approval, or the clone, install and run approval gates.
- Do not hand-edit a hash in `skills/SKILLS.lock.json`. No tool refreshes an existing entry yet, so a change to a locked skill file needs the owner's decision ([guide 5.5](docs/DEVELOPER_GUIDE.md#55-the-skill-lock)).
- Ask before deleting files, rewriting user evidence, force-pushing, pushing to `main`, or creating tags or releases.
- After adding, moving or removing a `.py` file under `src/`, `scripts/` or `skills/`, or a `.ts` file under `frontend/app`, `frontend/components` or `frontend/lib`, run `python scripts/gen_architecture.py`.
- Work in your own worktree on your own branch. Never stash or push.
- Do not edit `memory.md` directly from a worktree; the orchestrator writes it.

## Test commands

```bash
uv sync --locked --extra dev
uv run python scripts/check_all.py      # the full merge gate; add --skip-ui or --skip-desktop to skip a group
```

Single steps: `uv run pytest -q --ignore=tests/targets`, `uv run ruff check src tests scripts`, `uv run maf-harness lint-skills --strict`, `uv run python scripts/gen_architecture.py --check`. Frontend: `cd frontend && npm ci --include=dev && npm run typecheck && npm run build`. Desktop: `cd desktop && npm ci && npm test`.

Tests that bind local ports (the launcher, Playwright) need a shell without sandbox restrictions.
