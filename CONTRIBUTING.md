# Contributing

Agentic QE Platform: Python harness on Microsoft Agent Framework plus a Next.js UI. Private repository, internal team.

Read first: [CLAUDE.md](CLAUDE.md) (project rules), [docs/ARCHITECTURE_MAP.md](docs/ARCHITECTURE_MAP.md) (where code lives), [docs/RELEASING.md](docs/RELEASING.md) (release and tagging).

## Branch model

| Branch | Purpose | Merges into |
|---|---|---|
| `feature/*` | New work | `develop` via PR |
| `release/x.y` | Cut from `develop` for QA | `main` via PR |
| `hotfix/*` | Urgent fix cut from `main` | `main` via PR, then back-merged to `develop` |
| `main` | Released code only | Tagged `vX.Y.Z` |
| `develop` | Integration branch and default branch | |

- Never push directly to `main` or `develop`. Both are protected by rulesets (PR, one approving review, required checks).
- Hotfix flow: branch `hotfix/*` from `main`, PR to `main`, tag, then merge `main` into `develop` (or open a second PR to `develop`).

## Commits

Conventional Commits, one logical change per commit:

```
<type>(<optional scope>): <imperative summary>
```

Types: `feat`, `fix`, `docs`, `chore`, `test`, `refactor`, `ci`.

Examples: `feat(evidence): resolve bare symbol targets`, `fix(ui): keep run table sorted`, `ci: pin checkout action`.

## Local checks before opening a PR

Run from the repository root unless noted.

```bash
uv sync --extra dev
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run maf-harness lint-skills
uv run pytest -q --ignore=tests/targets
python scripts/gen_architecture.py --check

cd frontend
npm ci --include=dev
npm run typecheck
npm run build
npm test
```

Also run the Playwright suite when UI behaviour changes. Tests also run without uv: `.venv/bin/python -m pytest -q --ignore=tests/targets`.

## Memory log

Significant changes (new workflow stage, new skill, architecture change, release, non-trivial bug fix) need a dated entry in the "Core memory log" section of [memory.md](memory.md). Keep entries short: what changed, why, and where to look.

## Cross-OS rule

Every change must work on Windows, macOS and Linux:

- Use `pathlib` and `encoding="utf-8"` explicitly.
- Run subprocesses through `run_command` or `run_cancellable` (process groups). Never `shell=True`.
- Keep launchers thin (`start-windows.cmd`, `start-macos.command`, `start-linux.sh`) and delegate to `scripts/start_ui.py`.
- Node 24 and Python 3.12 are enforced by `engines`, `.npmrc` and `.nvmrc`.
- CI runs the matrix on ubuntu, windows and macos. Check the Windows path in the PR description if you touch file or process code.

## Secrets and run evidence

Never commit credentials, `.env`, local run evidence under `.maf-harness/runs/`, or `agent-framework.pdf`. Use placeholders such as `<api-key>` in docs and tests. The `secrets-scan` CI job will fail on token-shaped strings.
