# MAF Test Harness

A human-approved, evidence-driven **test-engineering harness** built on [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/) (MAF)
and Agent Skills. Give it a public GitHub repository. It analyzes the code, works out what must be installed (and installs it into an **isolated
environment after your approval**), designs tests per user persona, generates them in the repository's native framework, **runs them**, and hands
back a **ZIP with the complete repository, all generated and executed tests, and a QA report that lists the files and lines to update**.

```text
repo URL ─► analyze ─► requirements ─► environment ─► test plan ─► test design ─► test generation ─► run ─► QA report + ZIP
             (host)    (agent+skill)   (host, tool     (agent+     (agent+skill)   (agent+skill)     (host,  (host)
                                        approval)       skill)                                       tool approval)
```

Every model-authored artifact passes a human gate. Cloning, installing and running each need their own tool-call approval.
Whatever the host can compute exactly (does this import resolve, what are this class's real constructors, which URL routes exist, does this file
compile) is answered by deterministic **central tools** instead of a model retry — see [section 7](#7-central-tools-less-llm-guessing).
Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · roadmap: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) ·
defect register: [docs/audit/ERRORS.md](docs/audit/ERRORS.md) · live runs: [docs/audit/E2E_RESULTS.md](docs/audit/E2E_RESULTS.md) · observability: [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).

## 1. Prerequisites

| Need | Why | Check |
|---|---|---|
| Python 3.11+ and [`uv`](https://docs.astral.sh/uv/) | run the harness | `python3 --version`, `uv --version` |
| `git` | clone the target | `git --version` |
| An OpenAI-compatible chat gateway and key (tool-calling model) | the agents | your provider |
| Toolchain of the repository you test | the harness reports what is missing and how to install it, but never installs system tools | shown at the environment step |

Optional: Playwright browsers (`uv run playwright install chromium`) only if you run the UI browser tests.

## 2. Setup

```bash
git clone https://github.com/sarveshtalele/maf-harness-testing.git
cd maf-harness-testing
uv sync --extra dev            # creates .venv and installs the pinned dependencies
```

### Configure the model gateway (API key, base URL, model)

The harness talks to any **OpenAI-compatible chat endpoint** whose model can call tools (OpenAI itself, or a gateway such as LiteLLM or vLLM).
Three settings tell it where and how:

| Setting | Required | What to put there | Example |
|---|---|---|---|
| `OPENAI_API_KEY` | yes | the key your gateway issued | `sk-…` (never commit it) |
| `OPENAI_BASE_URL` | for a gateway; omit for OpenAI itself | the root URL the OpenAI SDK appends `/chat/completions` to. With a base URL the harness uses the *chat completions* API; without one it uses OpenAI's *Responses* API | `https://my-gateway.example.com` (some gateways need a `/v1` suffix: use exactly what your provider documents) |
| `OPENAI_MODEL` | yes (has a default) | the model name **as your gateway knows it**; `LLM_MODEL` is accepted as an alias | `deepseek-v4-flash-global` |

**Where to put them — a `.env` file in the project root (recommended).** From the project folder:

```bash
cp .env.example .env          # .env is git-ignored, so the key is never committed
```

then open `.env` and fill in the three values:

```bash
OPENAI_API_KEY=your-key-here
OPENAI_BASE_URL=https://your-openai-compatible-gateway
OPENAI_MODEL=deepseek-v4-flash-global
```

The CLI and the Streamlit app read it automatically at start-up (working directory first, then the project root). **Restart the app after editing it.**
Only `OPENAI_*`, `LLM_MODEL` and `HARNESS_*` are read from the file, and a variable that is already set in your shell **wins** over the file.

**Alternative — environment variables** in the shell that starts the app (useful in CI or for a one-off run):

```bash
export OPENAI_API_KEY="<your key>"
export OPENAI_BASE_URL="https://your-openai-compatible-gateway"
export OPENAI_MODEL="deepseek-v4-flash-global"
uv run streamlit run app.py
```

One run can also use a different model without touching the configuration: `uv run maf-harness run <repo> --model <name>`.

**Check that it works.** In the app, the sidebar section *Model gateway* shows the model, the base URL and `API key: configured`. To test the gateway itself
(a `200` means the key, URL and model are accepted; `401` a wrong key; `404` a wrong URL or model name; `429` the provider's quota):

```bash
set -a; source .env; set +a
curl -s -o /dev/null -w "%{http_code}\n" "$OPENAI_BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" -H "Content-Type: application/json" \
  -d "{\"model\":\"$OPENAI_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":1}"
```

(If your gateway's base URL already ends in `/v1`, drop the `/v1` from that command.) The key is used only for these model calls: it is never written into a run
directory and never forwarded to the install or test commands the harness executes.

Optional switches (defaults shown, all listed in `.env.example`):

| Variable | Default | Effect |
|---|---|---|
| `HARNESS_DAILY_TOKEN_LIMIT` | `50000000` | the harness's daily token cap, shared by all runs on this machine |
| `HARNESS_AGENT_TOOLS` / `HARNESS_AUTOFIX` / `HARNESS_BUILD_GATE` | `on` | central agent tools / mechanical import repairs / compile-collect gate (`off` for A/B comparison) |
| `HARNESS_SKILL_MODE` | `progressive` | MAF `SkillsProvider`, or `preloaded` for models that cannot call tools |
| `HARNESS_GENERATION_BATCH_SIZE`, `HARNESS_GENERATION_MAX_ATTEMPTS` | `3`, `3` | cases per model call, repair attempts per batch |
| `HARNESS_EXECUTION_TIMEOUT` | `900` | seconds per install/test command |
| `HARNESS_OTEL` | `off` | also export MAF's OpenTelemetry spans: `console` or `otlp` (needs `opentelemetry-sdk`); message content is never captured |

## 3. Run the application and open it in the browser

```bash
uv run streamlit run app.py
```

Open **<http://localhost:8501>**. (Headless server: `uv run streamlit run app.py --server.headless=true --server.address=127.0.0.1 --server.port=8501`.)

Walk-through:

1. Paste the repository URL — either the page URL (`https://github.com/owner/repo`, optionally `/tree/<branch-or-tag>`) or the clone URL (`.../repo.git`).
   Optionally paste the **running application URL** (`http://localhost:8080`) to allow UI/E2E cases.
2. **Approve clone** (*Approve repository inspection*) → review the analysis (*Approve and proceed*).
3. **Requirements review** shows actors, requirements, acceptance criteria, API contracts, the model's *environment needs*, and the host's deterministic
   **environment plan** (prerequisites with versions and install hints, isolation strategy, exact install commands — including nested `requirements*.txt`
   files and packages that the repository's own files document).
4. **Environment setup** is a tool-call approval: nothing is installed until you click *Install in isolated env* (or *Skip*). Libraries go into a venv,
   the project's `node_modules`, or harness cache directories — never the system interpreter. Missing system tools (JDK, Node, Maven…) are reported with
   install hints; install them and press *Re-check environment*.
5. Review **test plan**, then **test design** (cases per actor, with level, target and preconditions; unit cases are required). **Test source is generated only after you approve the design.**
6. Review the generated tests, then approve **Run the tests here** (you see every command first).
7. The **QA report** shows the verdict, per-case and per-requirement results, and **Files to update** (generated test files with line numbers, repository files
   with `file:line`, requirements still lacking a passing test). *Regenerate failing tests* sends only the failing cases back (≤3 rounds).
8. On completion, download the **ZIP** (repository + generated tests + reports).
9. Open the **Observability** tab of any run (also for saved runs) to see which skills and tools were invoked, what each stage cost, a timeline, and the requirement → case → file → result chain ([details](docs/OBSERVABILITY.md)).

The sidebar shows the token meter: **a daily cap of 50,000,000 tokens (shared by all runs on this machine, resetting at local midnight) is the only harness limit** — no per-run, per-call or call-count limits. The ledger lives in `~/.maf-harness/usage` (override with `HARNESS_USAGE_DIR`); counting starts when it is first written.
Your model gateway may enforce its own, separate quota; when it answers HTTP 429 the UI says so and retrying will not help until it resets (the harness never works around a provider limit).

## 4. Command line

```bash
uv run maf-harness run https://github.com/owner/repo                       # answer each gate in the terminal
uv run maf-harness run https://github.com/owner/repo --auto-approve --auto-heal 2      # unattended; exit 0 pass / 1 fail / 2 inconclusive
uv run maf-harness run https://github.com/owner/repo --app-url http://127.0.0.1:8080/app   # allow UI/E2E cases
uv run maf-harness verify-run .harness_runs/<run>     # evidence that MAF harness + Agent Skills were really used
uv run maf-harness trace .harness_runs/<run>          # skills, tools, cost and traceability report (--format json, --out FILE)
uv run maf-harness run <repo> --run-dir .harness_runs/<run>   # an existing run directory resumes at its pending gate
uv run maf-harness lint-skills                        # Agent Skills spec lint
```

`--auto-approve` is your explicit consent to clone the repository, install its dependencies and run its generated tests **on this machine as your user**
(local process, *not* a sandbox). Use a disposable machine/VM for untrusted repositories.

## 5. What you get

```
.harness_runs/<run>/
  qa_report.md / qa_report.json          verdict · per-case results · requirement coverage · files to update
  deliverables/<repo>-with-tests-<run>.zip
        <repo>/…                          complete repository at the analyzed commit + every generated test at its real path
        harness-report/qa_report.md       the report
        harness-report/RUN_TESTS.md       exact install/run commands to reproduce
        harness-report/*.json             requirements, environment, plan, design, generation catalog, executions, audit.jsonl
  workspace/  environment/  executions/  generation_attempts/  build_checks/  trace.jsonl  audit.jsonl  run.json
```

Verdicts: `PASS` (≥1 executed passing test, no failures) · `PASS_WITH_SKIPS` · `FAIL` · `INCONCLUSIVE` (setup failed, tools missing, or zero tests ran —
never reported as pass).

## 6. Design rules the harness enforces

* **Unit tests are required** in the design when the repository has source code; every actor (persona) needs cases; every requirement needs a case or a stated reason.
* Cases carry **level, actor, target, preconditions, data**; UI/E2E/accessibility cases only when a running application URL is supplied.
* Source is generated **one file per approved case** and validated before you see it: syntax, no placeholders/skips, import resolution, undefined names, mocked `sys.exit`, patch targets that do not exist, setup collections never used, URL paths that are not routes of the application, no Arquillian; after the environment install it must also collect/compile.
* Skills are loaded through MAF's `SkillsProvider` and verified from a tool trace; see `verify-run`.

## 7. Central tools (less LLM guessing)

Import and build errors are facts, so the harness answers them itself before asking a model again:

| Layer | What it does | Switch |
|---|---|---|
| Agent tools (MAF function tools) | `resolve_import`, `module_api`, `find_symbol`, `read_source`, `check_test`, `list_routes`, `installed_packages`, `probe_tools`, `try_build` — small per-stage selection, read-only or scratch-only | `HARNESS_AGENT_TOOLS` |
| Auto-fix | adds an unambiguous missing import (stdlib, mock, JDK, unique repository class, JUnit) or corrects a module path to the one repository module with that name; every fix is audited | `HARNESS_AUTOFIX` |
| Static gates | syntax, imports, undefined names, mocked `sys.exit`, patch targets, URL paths | always on |
| Build gate | `pytest --collect-only` / `mvn test-compile` in the prepared workspace, after the approved environment install; reports `file:line` and symbol detail | `HARNESS_BUILD_GATE` |

Prompts also carry the facts a model would otherwise guess: the application's routes and the real `BASE_URL` (which already contains the context path),
constructor/method signatures of the classes the code under test uses, declared and *forbidden* libraries. Retries raise the sampling temperature and
name the exact defect (for example the extra test methods), because a deterministic model otherwise repeats a rejected answer.
The host applies every check itself, so correctness never depends on an agent choosing to call a tool (in the measured run the agents called none).

## 8. Agent Skills

Ten skills are configured as MAF Agent Skills (`SkillsProvider`, progressive disclosure); the host verifies every assigned skill was really loaded.
Six come from [shivswami/awesome-agent-skills](https://github.com/shivswami/awesome-agent-skills) (pinned commit in `skills/SKILLS.lock.json`, audited offline
with its own `evaluator-skill`: 0 security findings); four are local.

| Stage | Skills loaded |
|---|---|
| requirements | `repository-requirements` (+ `api-contract-analyzer` if an OpenAPI/Swagger file exists) |
| test planning | `test-planning` |
| test design | `test-design-generator`, `test-data-generator` |
| test generation | `generic-test-generator`, `test-data-generator` (+ `junit-generator` for Java/Kotlin, + `playwright-test-generator` for UI/E2E cases) |
| QA (host) | `traceability-generator` and `coverage-analyzer` contracts, validated by the skills' own script/schema |

Scripts follow a strict policy: only reviewed scripts recorded in the lock (name + SHA-256) may run; the agent may call one pure script
(`test-data-generator/scripts/deterministic_helpers.py`) through MAF `run_skill_script`, the harness itself runs `validate_traceability.py`; all others are refused.

```bash
uv run maf-harness lint-skills                 # spec lint + lock verification (fails if a vendored file was edited)
git clone https://github.com/shivswami/awesome-agent-skills.git /tmp/awesome-agent-skills
uv run python scripts/vendor_skills.py --source /tmp/awesome-agent-skills    # re-vendor + re-audit + rewrite the lock
```

## 9. Development

```bash
uv run ruff check src tests app.py scripts && uv run ruff format --check src tests app.py scripts
uv run pytest -q --ignore=tests/targets                    # unit + component (real MAF) + workflow e2e; no network, no model
HARNESS_BROWSER=1 PLAYWRIGHT_BROWSERS_PATH=$PWD/.browser-cache uv run pytest tests/targets/test_harness_browser.py -q   # UI in a real browser
```

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Vendored Agent Skill modified after review" | a vendored file differs from `SKILLS.lock.json`; restore it or re-vendor with `scripts/vendor_skills.py` |
| "did not load the assigned Agent Skill" | the model skipped tool calls; retry, use a tool-capable model, or `HARNESS_SKILL_MODE=preloaded` (recorded as not using the provider) |
| Environment step says a tool is missing | install it (hint shown), then *Re-check environment* |
| `INCONCLUSIVE` with "not_executed" cases | build/collection failed; the report names the files and lines; use *Regenerate failing tests* |
| "Set OPENAI_API_KEY before starting the workflow" | no key was found: create `.env` from `.env.example` (see *Configure the model gateway*) or export the variable, then restart the app |
| The app still shows the old model or URL after editing `.env` | restart it: settings are read once at start-up; a variable exported in your shell overrides `.env` |
| HTTP 401 / 404 from the gateway | 401: wrong key. 404: wrong base URL (try adding or removing `/v1`) or a model name your gateway does not serve |
| "Daily token cap of 50,000,000 reached" | wait for local midnight or raise `HARNESS_DAILY_TOKEN_LIMIT`; all runs on this machine share the cap (`maf-harness run` prints today's usage) |
| "The model gateway refused the request (HTTP 429)" | the provider's own quota is exhausted; nothing in the harness can lift it. Choose *Retry* at the error gate after it resets (a *Stop* cannot be resumed) |
| A batch keeps failing generation | the audit log (`audit.jsonl`, `generation_attempts/`) names the gate; retries vary the temperature, and the error text says what to fix |
| UI tests need a live app | pass the running application URL; the harness does not start the system under test |

## 11. Security notes

Credentials come from the environment or the git-ignored `.env` and are never forwarded to install/test commands; commands are argv-only with timeouts and a plan hash checked after approval;
npm lifecycle scripts are disabled during install; generated files never touch the clone. Execution is a local process, not a sandbox — see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#4-guarantees-and-where-they-are-enforced).
