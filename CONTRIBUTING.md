# Contributing

Agentic QE Platform is a Python harness on Microsoft Agent Framework with a Next.js UI and an Electron desktop shell. The repository is private, for the internal team.

This page is the short version. The full rules, with examples and the reasons behind them, are in the [Developer guide](docs/DEVELOPER_GUIDE.md). Project rules for people and agents are in [CLAUDE.md](CLAUDE.md). Release steps and owner settings are in [docs/RELEASING.md](docs/RELEASING.md).

## Branches

- `develop` is the integration branch and the default branch. `main` holds released code only, and its README is the user guide. Details: [guide section 2](docs/DEVELOPER_GUIDE.md#2-branching-model).
- One branch per component change, named `<type>/<component>-<short-topic>`, for example `feat/qa-confidence-ledger` or `fix/desktop-orphan-reaping`. Types and components are listed in the guide.
- Do not push directly to `main` or `develop`.
- Open pull requests into `develop`.

## Commits

Use Conventional Commits: `<type>(<scope>): <imperative summary>`, with a body that explains why. Keep one logical change per commit. Add a `Co-Authored-By:` trailer when the work was AI-assisted. Details: [guide section 3](docs/DEVELOPER_GUIDE.md#3-commits).

## Before you open a pull request

Run the merge gate from the repository root:

```bash
uv run python scripts/check_all.py
```

It runs lint, format, the architecture map, the version files, the skill lock, pytest, the frontend typecheck, build and browser tests, and the desktop tests. Use `--skip-ui` or `--skip-desktop` only when that area is untouched, and say so in the PR. The steps and the setup are in [guide section 7](docs/DEVELOPER_GUIDE.md#7-the-merge-gate).

Every change must work on Windows, macOS and Linux. Use `pathlib`, `encoding="utf-8"` and argument lists, and never `shell=True`. [Guide section 5](docs/DEVELOPER_GUIDE.md#5-coding-standards-and-principles) covers this and the security rules. Never commit `.env`, run evidence under `.maf-harness/`, or `agent-framework.pdf`.

## Pull requests and review

1. Branch from `develop` with a name from the convention above.
2. Keep each commit to one logical change, and update the `CLAUDE.md` of each area you changed in the same commit.
3. Run the merge gate.
4. Open a pull request into `develop`. The template has the review checklist.
5. Wait for these required checks: `ci` (jobs `test` and `secrets-scan`) and `Local UI compatibility` (job `local-ui`, from `ui.yml`).
6. Get a review. `@sarveshtalele` owns every file (`.github/CODEOWNERS`). Security-sensitive changes (plugins, MCP, secrets, desktop, skills) get the owner's review before merge. The review checklist is in [guide section 8](docs/DEVELOPER_GUIDE.md#8-review-process).
7. Merge.

GitHub rulesets, required reviewers and secret scanning need a paid plan. The current plan does not enforce them, so follow these rules anyway.

A significant change (a new workflow stage, a new skill, an architecture change, a release, a non-trivial bug fix) is logged in `memory.md` by the orchestrator. Mention it in the pull request. Do not edit `memory.md` yourself.
