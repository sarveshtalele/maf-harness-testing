# Contributing

Agentic QE Platform is a Python harness on Microsoft Agent Framework with a Next.js UI and an Electron desktop shell. The repository is private, for the internal team.

Read first:

- [CLAUDE.md](CLAUDE.md): project rules.
- [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md): setup, checks and recipes (section 1 covers the dev environment).
- [docs/RELEASING.md](docs/RELEASING.md): releases, tags and owner settings.

## Branch model

| Branch | Purpose | Merges into |
|---|---|---|
| `feature/*` | New work | `develop`, by pull request |
| `release/x.y` | Cut from `develop` for QA | `main`, by pull request |
| `hotfix/*` | Urgent fix, cut from `main` | `main`, by pull request, then back to `develop` |
| `develop` | Integration branch and default branch | |
| `main` | Released code only | Tagged `vX.Y.Z` |
| `new-ui` | Mirrors `develop` during the transition | |

- Do not push directly to `main` or `develop` (the orchestrator merge described in CLAUDE.md is the only exception).
- Open pull requests into `develop`. The repository owner sets branch protection (see [RELEASING.md](docs/RELEASING.md)). On the current GitHub plan these rules may not be enforced by GitHub, so follow the rule anyway.
- After a hotfix reaches `main`, merge `main` back into `develop`.

## Pull requests

1. Branch from `develop`: `git checkout -b feature/<short-name> develop`.
2. Keep each commit to one logical change.
3. Run the local checks below.
4. Open a pull request into `develop`. Describe what changed and why. For changes to file or process code, say how the Windows path was checked.
5. Wait for these required checks to pass: `ci` (jobs `test` and `secrets-scan`) and `Local UI compatibility` (job `local-ui`, from `ui.yml`).
6. Get an approving review. Code owners review the files they own.
7. Merge.

Significant changes (a new workflow stage, a new skill, an architecture change, a release, a non-trivial bug fix) also need a short dated entry in the "Core memory log" in [memory.md](memory.md). Mention the change in the pull request so the maintainers add it.

## Commits

Use Conventional Commits:

```
<type>(<optional scope>): <imperative summary>
```

Types: `feat`, `fix`, `docs`, `chore`, `test`, `refactor`, `ci`.

Examples: `feat(evidence): resolve bare symbol targets`, `fix(ui): keep run table sorted`, `ci: pin checkout action`.

## Local checks

Run these from the repository root before you open a pull request. The full list and the Playwright steps are in [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md), section 1.6.

```bash
uv sync --extra dev
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run maf-harness lint-skills --strict
uv run pytest -q --ignore=tests/targets
uv run python scripts/gen_architecture.py --check
cd frontend
npm ci --include=dev
npm run typecheck
npm run build
cd ..
cd desktop
npm ci
npm test
cd ..
```

Run the Playwright tests when UI behaviour changes. Desktop `npm test` runs in `ui.yml`; run it locally when you change `desktop/`.

If you add, remove or move a `.py` or `.ts` file, regenerate the architecture map with `uv run python scripts/gen_architecture.py`. Never edit `docs/ARCHITECTURE_MAP.md` by hand.

## Cross-OS rule

Every change must work on Windows, macOS and Linux.

- Use `pathlib` and `encoding="utf-8"` explicitly.
- Run subprocesses through `run_command` or `run_cancellable`, which use process groups. Never use `shell=True`.
- Keep the launchers thin (`start-windows.cmd`, `start-macos.command`, `start-linux.sh`). They delegate to `scripts/start_ui.py`.
- Node 24 is enforced by `engines`, `frontend/.npmrc` and `frontend/.nvmrc`. Python is `>=3.11` (`requires-python`); CI tests 3.11 and 3.12, and the launchers use 3.12.

## Secrets and run evidence

Never commit credentials, `.env`, run evidence under `.maf-harness/`, or `docs/agent-framework.pdf`. Use placeholders such as `<api-key>` in docs and tests. The `secrets-scan` job in `ci.yml` fails on token-shaped strings.
