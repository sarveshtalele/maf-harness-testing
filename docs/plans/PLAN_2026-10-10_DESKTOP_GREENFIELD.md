# Plan: desktop app, plugin skills, greenfield specs, unit and integration focus (2026-10-10)

Status: **direction approved 2026-10-10.** Wave A (GitHub organisation) runs first, then the development segments in order. Each segment is reviewed before the next one starts.

Decisions:
- **Distribution:** internal team only. Unsigned dev builds come first; signing is added later.
- **Platforms:** Windows, macOS and Linux (AppImage) in the first release.
- **Jira:** Cloud only.
- **Dead-code removal:** still needs separate approval.

## Goals (from the request)

1. Unit and integration tests are generated and run accurately end to end for any repository.
2. Skills follow a plugin architecture. Dropping a new skill folder with `SKILL.md`, `scripts/` and `assets/` into the repository registers it in the workflow automatically.
3. The tool ships as a desktop application for Windows, macOS and Linux. It is Electron-based, no Azure is needed, and users can attach MCP servers such as Jira MCP and Playwright MCP and use their tools.
4. Greenfield mode: a business analyst supplies documents or Jira stories, and the tool creates specs, then requirements, then unit and integration tests. Brownfield, from an existing repository, stays as it is.
5. The New run form is limited to these choices: mode (brownfield or greenfield) and test types (Unit, Integration).
6. The GitHub repository is organised for production and development: branches, environments, CI, prerelease and release workflows.
7. A custom API key and model can be entered in the UI and are stored securely.
8. `docs/azure-plan-desktop.md` describes a later Azure cloud mode that works alongside installed desktop apps.
9. Open errors from the last live run are fixed first.

## Execution model

- **Orchestrator and final reviewer:** Opus (this session). It plans, briefs agents, merges, verifies, writes `memory.md` and does the final whole-project review with a gap table.
- **Implementers:**
  - Sonnet: multi-file logic, UI and packaging.
  - Haiku: mechanical work, such as docs, config, CI YAML and renames.
- **Isolation:** each task runs in its own git worktree under the session scratchpad, created by the orchestrator from `new-ui` and named `task-<id>`. Agents run with no access to other worktrees and never run `git stash`.
- **Per-task gate:** before merge the orchestrator runs:
  - ruff, the full pytest suite outside the sandbox, `tsc`, Playwright, `gen_architecture --check` and `lint-skills`;
  - Opus diff review.
- **Per-wave gate:** an Opus review agent with a fresh context reads the merged diff and reports defects, which are fixed before the next wave.
- **Memory:** one dated entry per task in the `memory.md` core memory log.

## Wave 0: finish open errors from the live run (mcp-skills-registry, run 17677c3b6d58)

| ID | Task | Model | Acceptance |
|---|---|---|---|
| 0.1 | Commit the target-source fallback. Bare symbol targets resolve to their defining file, which affects every language. Also handle one-line declarations (`const x = () => …;`), which currently fall back to a 70-line slice. | Sonnet | `ConnectorCards`, `DependencyGraph.get_reverse_deps` and `mcpUrl` extract the exact body. There are tests for TS, Python and Java. |
| 0.2 | Fix the validator hint when several files match a normalised import (several skills have `scripts/main.py`). Prefer the file linked to the case's requirements, otherwise list up to 3 candidates. | Sonnet | Batches 27 and 29 get the correct skill path in the hint. |
| 0.3 | Errors tab (redo of the agent that hit a rate limit): `ui/errors.py` normalises errors into categories. The UI gets one summary notice and a filterable, expandable Errors tab. | Sonnet | Playwright: 3 failures produce 1 notice and 3 rows. Filtering and expanding work. |
| 0.4 | Generation quality: cap the case IDs a model may cite (remap near-miss IDs deterministically), harden the one-test-per-file repair hint, and include the constructor signature in `collaborator_signatures` for classes under test. | Sonnet | Replaying the failing batches 33, 40, 48, 54 and 55 offline from the saved attempts passes the validators. |
| 0.5 | Rerun mcp-skills-registry end to end in the browser and record the pass rate. | Opus (driver) | The run reaches the QA report. Generated unit tests are executed and mapped, and the Errors tab explains every failed batch. |

## Wave 1: plugin skill architecture

| ID | Task | Model | Acceptance |
|---|---|---|---|
| 1.1 | Skill manifest: extend `SKILL.md` frontmatter metadata (agentskills.io-compatible) with `stages: [...]`, `role: primary\|companion`, `applies_when: {languages, frameworks, levels, files}`, `kind: model\|host` and `entrypoint` (for host scripts). | Sonnet | Spec in `docs/SKILL_PLUGINS.md`. `lint-skills` validates the new fields. |
| 1.2 | `skills/registry.py` discovers skills at startup from `skills/` and from a user plugin directory (`<user-data>/skills`). `skills/config.py` then selects skills from manifests instead of the hard-coded `SKILL_NAMES` and `COMPANION_PURPOSE`. Current skills get manifests, so behaviour stays the same. | Sonnet | Dropping a new skill folder with `stages:[test_generation]` makes it load in that stage with no code change. Covered by a test. |
| 1.3 | Lock policy for plugins: repository skills stay hash-locked. User plugins appear as unverified in the UI and need a one-time approval with their hash recorded before their scripts can run. | Sonnet | An unapproved plugin script cannot run. Approval persists per hash. |
| 1.4 | UI Skills page: "Add skill" (folder or zip import), approval status, and which stages each skill applies to. | Sonnet | Playwright test. |
| 1.5 | Repository layout as plugins: `src/maf_test_harness/plugins/` with entry points for stage steps, test runners (pytest, jest/vitest, junit, xunit) and MCP tool providers. Existing modules are re-exported, not rewritten. | Sonnet | `gen_architecture` map updated. No behaviour change in the full suite. |

## Wave 2: unit and integration focus, greenfield mode

| ID | Task | Model | Acceptance |
|---|---|---|---|
| 2.1 | New run form shows: mode (Brownfield repository / Greenfield specs), test types (Unit ☑, Integration ☑), case budget and run name. Other levels (ui, e2e, contract, performance) are hidden and filtered out at design time. The repository URL is optional in greenfield mode. | Sonnet | Design produces only unit or integration levels. Covered by tests. |
| 2.2 | Integration tests per stack: rules in `LANGUAGE_RULES` and skill references for API test clients (FastAPI TestClient, Spring MockMvc/Testcontainers if declared, supertest, WebApplicationFactory), DB fixtures, and the existing-test conventions. | Sonnet | The mcp-skills-registry rerun includes executed integration tests. |
| 2.3 | Greenfield intake: upload documents (md, docx, pdf, txt) or fetch Jira stories (by JQL or keys, through the Jira MCP or REST with the user's token). A deterministic parser plus a `spec-authoring` skill produce specs (feature, acceptance criteria, rules, data), which go to a requirement model and a review gate. | Sonnet | Fixture docs and Jira JSON produce specs and requirements with stable IDs that trace back to their source section or story key. |
| 2.4 | Greenfield output: from approved specs, the design step creates unit and integration cases against the target repository, when one is linked, or an empty project scaffold (pytest or jest skeleton). Tests run once code exists. | Sonnet | End to end with a sample spec and a starter repository. |
| 2.5 | Accuracy loop: one benchmark set of 4 repositories (mcp-skills-registry, mcp-server-market-v2, store-pos, petstore), run headless by CI nightly in the dev environment. Metrics: generated count, executed, passed, failed batches and reviewer confidence. | Sonnet + Opus review | A results table in `docs/benchmarks/`. A regression fails the nightly job. |

## Wave 3: desktop application (Electron)

| ID | Task | Model | Acceptance |
|---|---|---|---|
| 3.1 | `desktop/`: Electron main and preload in TypeScript, and a process manager that starts the bundled Python API and Next server on random loopback ports with a per-launch token. It kills the process trees on quit (reusing the launcher's orphan logic) and adds a single-instance lock. | Sonnet | The app opens a native window. Quitting leaves no processes on Windows or macOS. |
| 3.2 | Backend packaging: PyInstaller one-folder build of `maf_test_harness.ui.api`, plus Next `output: 'standalone'`. Toolchains (Playwright browsers, JDK, .NET) are downloaded on demand into `<user-data>/toolchains`, using the existing toolchains module. | Sonnet | The installer is about 250–350 MB without browsers. A first run works offline except for model calls and downloads. |
| 3.3 | Per-user data directories (Windows `%APPDATA%\AgenticQE`, macOS `~/Library/Application Support/AgenticQE`) for runs, logs, cache and plugins. Existing environment overrides are kept. | Haiku | Path tests use `PureWindowsPath`. |
| 3.4 | Settings UI for API key and model: provider, base URL, model, per-stage models, and a key stored in the OS keychain (Windows Credential Manager or macOS Keychain, via Electron `safeStorage` or keytar). The key is never written to disk in plain text or shown back in the UI, and `.env` still works for development. | Sonnet | The key survives restart. A redaction test covers logs and evidence. |
| 3.5 | MCP integration: an MCP servers settings page to add stdio or HTTP servers (Jira, Playwright, others) with environment variables and secrets in the keychain. They are wired into the MAF agent as MCP tools (`MCPStdioTool`/`MCPStreamableHTTPTool`) with approval rules: read-only tools are auto-approved and mutating tools need approval. Presets for Atlassian (Jira) MCP and `@playwright/mcp`. | Sonnet | A Jira MCP fixture fetches stories in greenfield intake. The Playwright MCP tool appears in the stage tool list, gated. |
| 3.6 | Installers through electron-builder: Windows NSIS `.exe`, macOS `.dmg` (x64 and arm64) and Linux AppImage. Builds are unsigned for the internal team; the signing hooks stay in place for later. Auto-update through private GitHub Releases. | Sonnet | CI produces installers. Smoke tests install and launch the app on clean runners. |
| 3.7 | Security: authenticated localhost communication (token plus Origin and Host checks, kept), a CSP in Electron, `contextIsolation`, no `nodeIntegration`, and an untrusted-repository warning with optional disposable VM guidance. | Sonnet + Opus review | Security review checklist passes. |

## Wave A (runs first): GitHub organisation for production and development

| ID | Task | Model | Acceptance |
|---|---|---|---|
| 4.1 | Branches: `main` (protected, release), `develop` (integration), `release/*` (QA) and `feature/*`. `new-ui` is merged into `develop` after review. Document the PR rules and required checks. | Haiku | `docs/CONTRIBUTING.md` and branch protection settings listed for the owner to apply. |
| 4.2 | GitHub Environments: `dev` (nightly benchmark, internal unsigned installers) and `prod` (signed releases, manual approval). The release-please changelog is optional. | Haiku | `.github/workflows/{ci,nightly-benchmark,desktop-prerelease,desktop-release}.yml`. |
| 4.3 | Repository cleanup from the dead-code review (table A only, with your approval, no deletions otherwise). The root folders `design/`, `patches/` and `azure-plan/` move to `docs/archive/` or are removed with your approval. | Haiku | Done only after explicit approval. |
| 4.4 | `CLAUDE.md`, `README.md` and the docs-maintainer agent are updated, and the architecture map is regenerated. | Haiku | Map `--check` passes. |

## Wave 5: cloud plan document

| ID | Task | Model | Acceptance |
|---|---|---|---|
| 5.1 | `docs/azure-plan-desktop.md` covers a hybrid design: the desktop app stays local-first and can optionally connect to an Azure workspace for shared run history, a team dashboard, central skills and plugins, and remote isolated test workers. The design includes Entra ID sign-in, an API (Container Apps), storage (Blob plus Postgres), worker isolation (Container Apps jobs or ephemeral VMs), sync protocol, cost estimate and phased rollout. | Sonnet (writer) + Opus review | Reviewed document. No infrastructure changes. |

## Final review (Opus)

A whole-project review with a fresh context produces an error and gap table (severity, file, evidence, fix, status). Remaining fixes are dispatched, then everything is pushed.

## Order, effort and dependencies

| Wave | Depends on | Tasks in parallel | Estimated agent time |
|---|---|---|---|
| 0 | — | 0.1–0.4, then 0.5 | about 1.5 h, plus a live run of about 2 h |
| 1 | 0 | 1.1, then 1.2, 1.3, 1.5 in parallel, then 1.4 | about 2 h |
| 2 | 1 | 2.1, 2.2, 2.3 in parallel, then 2.4, then 2.5 | about 3 h |
| 3 | 0, partly 2 | 3.1, 3.3, 3.4 in parallel, then 3.2, 3.5, then 3.6, 3.7 | about 4 h |
| A (first) | — | 4.1, 4.2, 4.4 (4.3 after approval) | about 1 h |
| 5 | 3 | 5.1 | about 0.5 h |

## Risks

- **Packaging:** PyInstaller with MAF, pydantic and agent-framework plugins needs hidden-import tuning. Mitigation: a CI smoke test on both operating systems.
- **Code signing:** it needs an Apple Developer ID and a Windows certificate, which are owner-provided secrets. Without them, only unsigned dev builds are possible.
- **Jira MCP:** it requires the user's Atlassian authorization in the app. Credentials stay in the keychain.
- **Untrusted code:** generated tests run repository code locally. The desktop app does not sandbox it, so the warning and VM guidance are kept.

## Decisions (resolved)

1. Distribution: internal team only.
2. Linux installer: needed now (AppImage).
3. Jira: Cloud only.
4. Approval for the dead-code removals (table A in `docs/DEAD_CODE_REVIEW_2026-10-07.md`), and whether to archive or delete the root folders.

## Revisions from the Opus plan review (2026-10-10)

**Added or changed tasks**
- **A.0 clean state.**
  - Commit the in-progress fix 0.1 and the plan.
  - Cut `develop` from `new-ui`.
  - All task worktrees branch from `develop` from now on.
- **A.1 repository files** (one PR, `new-ui` → `develop`). Haiku except where noted:
  - root `CONTRIBUTING.md`: branch model `feature/*` → `develop`, `release/x.y` → `main`, tags `vX.Y.Z`, `hotfix/*`; Conventional Commits; required checks;
  - `.github/CODEOWNERS`, `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/{bug,feature,skill-request,config}.yml`;
  - `.github/dependabot.yml` (uv/pip, npm `/frontend`, actions; weekly; target `develop`);
  - `docs/RELEASING.md` with the owner checklist;
  - a version check that the tag equals the `pyproject.toml` and `frontend/package.json` versions.
- **A.2 workflows** (**Sonnet**):
  - `ci.yml` and `ui.yml`:
    - triggers: push to `main`, `develop`, `release/**` plus `pull_request`, removing today's double runs;
    - `concurrency: cancel-in-progress`;
    - `permissions: contents: read`.
  - `nightly.yml`: schedule plus dispatch, `environment: dev`, runs the existing full pytest. Benchmarks are added in 2.5.
  - `desktop-prerelease.yml` and `desktop-release.yml`:
    - matrix of native runners: windows-latest, macos-14 (arm64), macos-13 (x64), ubuntu-latest;
    - guarded with `hashFiles('desktop/package.json') != ''` so they are no-ops until Wave 3;
    - environments `dev` and `prod`; release output goes to a draft GitHub Release.
- **Owner actions** (GitHub settings that cannot be done from the repository):
  - create `develop` and make it the default branch;
  - rulesets on `main` and `develop`: PR required, CODEOWNERS review, required checks `test`, `secrets-scan`, `local-ui`, no force-push;
  - environments `dev` (no reviewers) and `prod` (required reviewer);
  - enable Dependabot and secret scanning;
  - set Actions permissions.
- **Wave 1 corrections**
  - Skill plugin metadata goes in a separate `plugin.yaml` next to `SKILL.md`. MAF drops nested `metadata` values, so they cannot live in the frontmatter.
  - 1.3: any folder in `skills/` without a lock entry is treated as unverified and must be approved once. Approvals are recorded per hash in `<user-data>/plugins.lock.json`, reusing the existing `reviewed_scripts` mechanism. A CI check enforces lock completeness for repository skills. Reviewed by Opus.
  - 1.5 is reduced to a test-runner registry only; the speculative plugin re-export layer is cut.
- **Wave 2 corrections**
  - 2.1: an allowlist of levels {unit, integration}, with `api` mapped to integration.
  - 2.3 defines the ID scheme `SPEC-<sourcehash8>-<n>` with `source {doc, section_anchor | jira_key}`. It adds docx and pdf parsers to the dependencies and uses Jira Cloud REST first; MCP comes in 3.5.
- **Wave 3 corrections**
  - New 3.0 (**Sonnet**): a `paths.py` resource and user-data resolver that works with `sys._MEIPASS`. It replaces every `Path(__file__).parents[N]` ROOT use in cli, api, settings_api, evidence_api, prompt_settings, runtime, monitoring/report and settings/.env. Task 3.3 merges into 3.0.
  - 3.2: Next `output: 'standalone'`, with `server.js` run under Electron's Node (`ELECTRON_RUN_AS_NODE=1`) and `.next/static` and `public` copied in. The proxy keeps the `HARNESS_API_PORT`/`HARNESS_API_TOKEN` environment contract.
  - 3.4: a `settings.current()` runtime resolver (keychain, then environment, then `.env`). Electron `safeStorage` decrypts the key and passes it to the Python child at spawn, restarting it when the key changes. The Python `keyring` package is not used.
  - 3.5:
    - add `mcp>=1.24,<2` to `pyproject.toml` and the PyInstaller hidden imports;
    - use MAF `MCPStdioTool`/`MCPStreamableHTTPTool` with `approval_mode` allowlists of read-only tool names per preset;
    - review `MCPSkillsSource`;
    - reviewed by Opus.
  - 3.6: unsigned installers carry quarantine-removal instructions for macOS. Auto-update is deferred and replaced by a manual "Check for updates" link to Releases.
  - 3.7: remove `Access-Control-Allow-Origin: *` (`ui/api.py`).
- **Ordering:** 4.4 (map and CLAUDE.md) runs per task instead of once in Wave A. Task 3.6 depends on 3.0 and 3.2. Task 3.5 depends on 3.4.
