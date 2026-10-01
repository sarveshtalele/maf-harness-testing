# Local Next.js workspace

The Next.js UI keeps the existing seven-stage MAF workflow, human gates, hook engine, strict environment checks, generation validators, recovery, reports and checkpoints. The CLI uses the same backend.

## Start on macOS or Windows

Prerequisites: Git, Python 3.11+ (3.12 tested), uv and Node.js 24 LTS.

The simplest production startup is `./start-macos.command` on macOS or `.\start-windows.cmd` in Windows PowerShell. Each installs locked dependencies, builds Next.js and starts both servers. Existing `.env` files are preserved. Both accept `--port` and `--api-port`.

For manual setup, from the repository root in Terminal or PowerShell:

```text
uv sync --extra dev
npm --prefix frontend ci
```

Copy `.env.example` to `.env` using your editor and fill in `OPENAI_API_KEY`, `OPENAI_MODEL` and your gateway's `OPENAI_BASE_URL`. Never commit that file. The UI shows whether the key is configured, not its value.

```text
uv run python scripts/start_ui.py
```

Open http://127.0.0.1:3000. Ctrl+C stops both processes. The launcher uses the current Python environment, finds `npm`/`npm.cmd`, binds both services to loopback and supplies a fresh private API token to the server processes. It does not persist the token or expose it to the browser.

For a production build:

```text
npm --prefix frontend run build
uv run python scripts/start_ui.py --production
```

Optional launcher flags: `--port 3001 --api-port 8766`. Port values are validated and checked before launch. Use the launcher rather than starting Next.js alone; the API token must match both processes.

## Data and compatibility

New UI runs default to `~/.maf-harness/runs` for each operating-system user; `.harness_runs` in this checkout remains discoverable so existing runs can be inspected and resumed. An explicit `HARNESS_RUNS_DIR` overrides the default. Use separate OS accounts or separate machines for separate users. There is no application login or shared team server in this phase.

The API reads the existing checkpoint format and passes decisions to `Harness.respond`. Browser reloads do not kill an operation. One operation runs at a time, matching the previous local UI. Old runs without full traces show summary observability. Interrupted consumed gates are never automatically replayed. Reconnect rebuilds the runtime connection, not the approved action.

The CLI defaults to project-local storage. Set their `HARNESS_RUNS_DIR` to the UI directory if you want to work on the same runs. Stop the UI operation before using another host to mutate that run. No checkpoint migration is required.

## Layout and evidence

Drag the sidebar’s right edge or the divider between workspace columns to resize with the mouse. Drag a panel’s visible lower-right handle to adjust both width and height. Handles capture the pointer so dragging continues outside their bounds, and support arrow keys. Layout changes use mouse handles or arrow keys; there is no adjustment form or slider menu. Panel sizing is controlled directly by its drag handle; the former panel adjustment form is removed. Each panel collapses or expands into a dialog. The original neutral light/dark palette from the approved concept is retained. Preferences are saved in this browser, scoped to the run and panel. Small screens stack the panels; Escape closes expanded views.

The **Agent skills** page at `/?page=skills` shows all installed project packages as searchable cards, with primary/companion/host roles and descriptions. Each card links to its own reloadable `/?page=skills&skill=<name>` documentation page, rendering SKILL.md headings, lists, tables and code blocks with package resources and reviewed-script checks. The upstream branch/commit/library provenance is omitted from the website. It reads the actual skill registry and does not change packages.

Run views include Review, Artifacts, Generated tests, QA report, Test reports, Logs, Observability and Audit. All nested artifact fields and raw JSON remain available. Audit has expandable events, filters, complete-record search and JSONL export. Observability includes stages/cache/model calls, skills, tool filters, hooks/recovery/diagnosis, timeline/lane filters, traceability/state filters, integrity, telemetry and exports. Complete and passing are distinct; FAIL and INCONCLUSIVE keep their own verdicts.

## Local execution limits

Repository input still supports public GitHub repositories. Application URLs must be localhost/127.0.0.1 on the execution host. Execution support depends on the detected stack and available tools. Dependencies use isolated environments, but commands run as the OS user: this is not a security sandbox for arbitrary untrusted repositories. These existing backend limits have not changed.

The adapter accepts a server-only bearer token and rejects browser Origin headers. Next.js proxies same-origin requests, validates local host/origin and never returns its bearer token. State responses omit model sessions, prompts and internal checkpoint fields. Presentation data uses the existing secret redactor. Downloads are limited to known artifacts or the selected run's ZIP; arbitrary filesystem paths are not accepted.

## Checks

```text
uv sync --extra dev --extra e2e
uv run pytest -q --ignore=tests/targets
uv run ruff check src tests app.py scripts
uv run ruff format --check src tests app.py scripts
uv run maf-harness lint-skills
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

For Chromium browser checks, from `frontend`:

```text
npx playwright install chromium
npm test
```

The browser fixture replaces only the model/clone/install-plan inputs with deterministic local data. The real workflow generates source, runs pytest as a subprocess, builds QA/ZIP and handles gates through HTTP. This is separate from live GitHub/model qualification. `HARNESS_TEST_PYTHON` can override the default `.venv` Python path. CI checks macOS and Windows; a CI configuration is not evidence of a completed Windows run.

## Reports, failures and logs

QA failures are shown as cards with recorded output, affected paths and the existing backend's improvement recommendations. These are triage advice, not a proven cause. Tables have sticky sortable headings, row inspection dialogs, search and pagination. Filters use visible choice chips.

**Test reports** discovers contained evidence in the selected run's execution/environment/build directories, `browser-reports` and known workspace report folders. Playwright HTML runs inside a sandboxed iframe with restricted network access, separately from the app. Report theme settings use memory-only storage; embedded Playwright JSON is redacted before rendering. Screenshot cards show the actual saved images; traces can be downloaded for the local Playwright viewer. Missing assets remain visibly missing; reports are not fabricated for unit-only runs.

The Node Playwright adapter preserves the repository’s root Playwright configuration through a separate capture wrapper and requests JUnit+HTML, failure screenshots and retained failure traces, then copies fresh browser output into the immutable execution attempt directory. Other adapters expose assets they already produce. This does not repair the mixed-ecosystem runner limitation documented in the backend review.

**Logs** provides line numbers, error highlighting, filename/attempt search and in-output filtering. Large logs show the first 512 KB with a full download. Evidence files over 32 MB are omitted from the inline inventory. Secret redaction applies to textual presentation; arbitrary source/configuration paths and external symlinks are not served.

CI publishes Playwright HTML/JUnit, screenshots and traces as per-OS artifacts, including on failed runs. The macOS startup timeout was increased from 30 to 120 seconds for cold framework imports; server output is retained for diagnosis.
