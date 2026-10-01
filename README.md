# MAF Test Harness

A local repository-testing workspace with a **Next.js frontend** and the existing **Python / Microsoft Agent Framework backend**. `new-ui` is the primary UI branch. Give the harness a public GitHub repository, review each stage, approve installation and test execution, then inspect QA results, failure recommendations, logs and browser evidence.

The UI includes mouse-resizable panels, light/dark appearance, per-user run history, agent skill cards, audit and observability. The CLI uses the same backend. Execution runs as your OS user with isolated dependencies; use a disposable machine for repositories you do not trust.

## Quick start: macOS and Windows

Install [Git](https://git-scm.com/), [Node.js 24 LTS](https://nodejs.org/) and [uv](https://docs.astral.sh/uv/getting-started/installation/). The scripts let uv provision Python 3.12, install the locked Python/frontend dependencies and build the UI.

```text
git clone --branch new-ui https://github.com/sarveshtalele/maf-harness-testing.git
cd maf-harness-testing
```

Copy `.env.example` to `.env`, then edit it with your gateway values:

```dotenv
OPENAI_API_KEY=your-key-here
OPENAI_BASE_URL=https://your-openai-compatible-gateway
OPENAI_MODEL=your-tool-capable-model
```

macOS Terminal:

```bash
cp .env.example .env   # first setup only; do not overwrite an existing .env
chmod 600 .env
# Edit .env, then:
./start-macos.command
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env   # first setup only
notepad .env
.\start-windows.cmd
```

Open **http://127.0.0.1:3000**. One launcher starts both **Next.js on 3000** and the **Python API on 8765**, with matching private server credentials. Keep the terminal open; Ctrl+C stops both servers. A missing `.env` is created from the example without overwriting existing configuration. Restart after changing gateway settings. Never commit credentials.

To use different ports:

```text
./start-macos.command --port 3001 --api-port 8766
.\start-windows.cmd --port 3001 --api-port 8766
```

The scripts install/build on each launch to keep the checkout and dependencies consistent. For faster development after initial setup: `uv run python scripts/start_ui.py`.

### Using the workspace

1. **Configuration:** check the gateway model and whether a key is configured; the key itself is hidden.
2. **New run:** enter a public GitHub URL and optionally a running local application URL. Approve the clone action.
3. **Review:** inspect requirements, the environment plan, test plan/design and generated source. Approve, request changes or stop at each gate.
4. **Run tests:** review exact commands before approving execution. Missing prerequisites and errors remain visible.
5. **QA report:** inspect per-case results, requirement coverage, recorded failure output and recommendations with affected files. Recommendations require review; an assertion failure alone does not establish an application defect.
6. **Test reports:** view saved Playwright HTML inside the UI, screenshots and JUnit, or download a trace for `npx playwright show-trace trace.zip`. Browser evidence appears only when a browser suite produced it.
7. **Logs:** choose an installation/build/execution log, search its line-numbered output or download the full file. **Audit** and **Observability** show approvals, agents, tools and usage.
8. Download the deliverable ZIP when available. Browse **Agent skills** to inspect all installed packages and their provenance.

Drag sidebar/column edges and panel corners; use arrow keys for accessible resizing. Preferences persist in your browser. Runs default to `~/.maf-harness/runs`, separately for each OS user. This is a local app without shared-server login.

[Detailed setup and UI guide](docs/NEXTJS_UI.md) · [Backend findings and limits](docs/BACKEND_REVIEW_2026-10-01.md) · [Cleanup review — no files deleted](docs/DEAD_CODE_REVIEW_2026-10-01.md)

The backend selects one execution ecosystem today. Mixed-stack coverage, QA edge cases and other reviewed gaps are listed above; a passing generated suite does not prove that every repository component was exercised. Playwright HTML/screenshots/traces are captured for the Node Playwright adapter; Python and other adapters show the report assets they actually produce.

## Development and verification

```text
uv sync --python 3.12 --extra dev --extra e2e
npm --prefix frontend ci
uv run python scripts/start_ui.py
```

The development launcher starts both services. To verify a production build:

```text
uv run ruff check src tests app.py scripts
uv run ruff format --check src tests app.py scripts
uv run pytest -q --ignore=tests/targets
uv run maf-harness lint-skills
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

From `frontend`, run `npx playwright install chromium`, then `npm test`. This produces `playwright-report/index.html`, JUnit, failure screenshots and traces. Browser checks exercise the real workflow with deterministic model inputs; they are distinct from live model/GitHub runs. GitHub Actions runs backend checks on Ubuntu/Windows and Next.js browser journeys on macOS/Windows, publishing per-platform browser reports.

## Command line

```text
uv run maf-harness run https://github.com/owner/repo
uv run maf-harness run https://github.com/owner/repo --app-url http://127.0.0.1:8080
uv run maf-harness verify-run /path/to/run
uv run maf-harness trace /path/to/run
uv run maf-harness lint-skills
```

The CLI defaults to project-local `.harness_runs`. Set `HARNESS_RUNS_DIR` to the UI's run directory to share checkpoints; stop an active UI operation before another process changes that run. `--auto-approve` explicitly authorizes clone/install/test commands as your OS user.

## Configuration and troubleshooting

`LLM_MODEL` is accepted as an alias for `OPENAI_MODEL`. Use the base URL exactly as your gateway documents it, including `/v1` when required. Shell variables override `.env`; restart both servers after changing settings. See `.env.example` for token limits, toolchains, hooks and tracing options.

| Symptom | Action |
|---|---|
| Old UI still visible | Stop the launcher with Ctrl+C, rerun the platform script to rebuild, then reload port 3000. Close previews on other ports. |
| Port already in use | Stop the previous launcher, or pass different `--port` and `--api-port` values. |
| Gateway key missing, HTTP 401/404 | Check `.env`, gateway URL/model and key; restart. Never put credentials in a repository or target test environment. |
| Environment incomplete | Read the failed command and missing-prerequisite details, install the indicated tool or approve the revised environment plan, then retry. |
| Tests fail or cannot collect | Inspect QA case output and Logs. Check the application, test expectations and environment before treating a failure as an application defect. |
| No Playwright report | The selected attempt must actually run a browser suite. Unit-only runs do not produce browser screenshots. |
| No runs for another teammate | History is separated by OS account. This version has no shared-server authentication. |

## Backend documentation

| Guide | Contents |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | Workflow, checkpoint format and execution guarantees |
| [Agent catalog](docs/CATALOG.md) | Stage personas, central tools and skill assignments |
| [Environments](docs/ENVIRONMENTS.md) | Isolation, toolchains and dependency checks |
| [Error playbook](docs/ERROR_PLAYBOOK.md) | Error classification and recovery |
| [Automation](docs/AUTOMATION.md) | Generation validation and bounded repair |
| [Hooks](docs/HOOKS.md) | Approval, redaction and write-scope controls |
| [Observability](docs/OBSERVABILITY.md) | Traces, tools, usage and requirement coverage |

This README documents the `new-ui` branch's Next.js application. Historical source files are retained pending explicit deletion approval; they are not installed or launched as this branch's UI.
