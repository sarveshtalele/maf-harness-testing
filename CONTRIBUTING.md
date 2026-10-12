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
| `develop` | Integration branch and default branch | |
| `main` | Released code only; its README is the user guide | Tagged `vX.Y.Z` (see [RELEASING.md](docs/RELEASING.md)) |

`release/x.y` and `hotfix/*` branches are not used today; releases are made from `develop` merged into `main`.

- Do not push directly to `main` or `develop` (the orchestrator merge described in CLAUDE.md is the only exception).
- Open pull requests into `develop`. The repository owner sets branch protection (see [RELEASING.md](docs/RELEASING.md)). On the current GitHub plan these rules may not be enforced by GitHub, so follow the rule anyway.

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

Types: `feat`, `fix`, `docs`, `chore`, `test`, `refactor`, `ci`, `build`, `style`.

Examples: `feat(evidence): resolve bare symbol targets`, `fix(ui): keep run table sorted`, `ci: pin checkout action`.

## Local checks

Run the merge gate from the repository root before you open a pull request:

```bash
uv sync --locked --extra dev
uv run python scripts/check_all.py
```

`check_all.py` runs, in order: ruff check and format, the architecture map check, the version files check, `lint-skills --strict`, pytest, the frontend typecheck, build and Playwright tests, and the desktop shell tests. It prints a summary and exits non-zero if any step fails. Use `--skip-ui` or `--skip-desktop` only when that area is untouched. The frontend steps need `npm ci --include=dev` in `frontend/` and `npm ci` in `desktop/` first. Details are in [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md), section 1.6.

If you add, remove or move " + MAP_RULE + ", regenerate the architecture map with `uv run python scripts/gen_architecture.py`. Never edit `docs/ARCHITECTURE_MAP.md` by hand. `desktop/src` is not part of the map.

## Cross-OS rule

Every change must work on Windows, macOS and Linux.

- Use `pathlib` and `encoding="utf-8"` explicitly.
- Run subprocesses through `run_command` or `run_cancellable`, which use process groups. Never use `shell=True`.
- Keep the launchers thin (`start-windows.cmd`, `start-macos.command`, `start-linux.sh`). They delegate to `scripts/start_ui.py`.
- Node 24 is enforced by `engines`, `.npmrc` (`engine-strict`) and `.nvmrc` in both `frontend/` and `desktop/`. Python is `>=3.11` (`requires-python`); CI tests 3.11 and 3.12, and the launchers use 3.12.

## Secrets and run evidence

Never commit credentials, `.env`, run evidence under `.maf-harness/`, or `docs/agent-framework.pdf`. Use placeholders such as `<api-key>` in docs and tests. The `secrets-scan` job in `ci.yml` fails on token-shaped strings.
