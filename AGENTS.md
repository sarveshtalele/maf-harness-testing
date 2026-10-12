# Agent guide (Copilot, Codex and other coding assistants)

Short entry point. Authoritative guidance: [CLAUDE.md](CLAUDE.md) (root rules and merge gate), the `CLAUDE.md` in each directory you edit, and [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) (how-to recipes).

## What the project is

Agentic QE Platform: a Next.js UI and a loopback Python API over a checkpointed Microsoft Agent Framework workflow. It turns a public GitHub repository (brownfield) or specs (greenfield) into reviewed unit and integration tests, runs them with approval, and writes a QA report. It ships as a local launcher and as an Electron app in `desktop/`. Backend code is in `src/maf_test_harness/`; agent skills are in `skills/`.

## Must-follow rules

- Every change must work on Windows, macOS and Linux: use `pathlib`, `encoding="utf-8"`, argv lists and never `shell=True`.
- Never commit secrets, `.env`, run evidence or `agent-framework.pdf`, and never print API keys.
- Never relax repository dependency pins or replace missing application dependencies with fake tests.
- Never bypass the skill lock (`skills/SKILLS.lock.json`), plugin approval, or the clone, install and run approval gates.
- Ask before deleting files, rewriting user evidence, force-pushing or pushing to `main`.
- After editing a locked skill file, refresh only its hash in `skills/SKILLS.lock.json`.
- After adding, moving or removing a `.py` file under `src/`, `scripts/` or `skills/`, or a `.ts` file under `frontend/app`, `frontend/components` or `frontend/lib`, run `python scripts/gen_architecture.py`.

## Test commands

```bash
uv sync --locked --extra dev
uv run python scripts/check_all.py      # the full merge gate; add --skip-ui or --skip-desktop to skip a group
```

Single steps, when you need only one: `uv run pytest -q --ignore=tests/targets`, `uv run ruff check src tests scripts`, `uv run maf-harness lint-skills --strict`, `uv run python scripts/gen_architecture.py --check`. From the frontend: `cd frontend && npm ci --include=dev && npm run typecheck && npm run build`. From the desktop folder, which is `../desktop` when you start in `frontend/`: `cd ../desktop && npm ci && npm test`.

Tests that bind local ports (launcher, Playwright) need a shell without sandbox restrictions.
