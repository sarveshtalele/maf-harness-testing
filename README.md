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
Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · roadmap: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) ·
defect register: [docs/audit/ERRORS.md](docs/audit/ERRORS.md) · live runs: [docs/audit/E2E_RESULTS.md](docs/audit/E2E_RESULTS.md).

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

Set the model gateway in your shell (never commit it; see `.env.example`):

```bash
export OPENAI_API_KEY="<your key>"
export OPENAI_BASE_URL="https://your-openai-compatible-gateway"   # omit for api.openai.com
export OPENAI_MODEL="deepseek-v4-flash-global"                    # any tool-capable chat model
```

## 3. Run the application and open it in the browser

```bash
uv run streamlit run app.py
```

Open **<http://localhost:8501>**. (Headless server: `uv run streamlit run app.py --server.headless=true --server.address=127.0.0.1 --server.port=8501`.)

Walk-through:

1. Paste the repository URL — either the page URL (`https://github.com/owner/repo`, optionally `/tree/<branch-or-tag>`) or the clone URL (`.../repo.git`).
   Optionally paste the **running application URL** (`http://localhost:8080`) to allow UI/E2E cases.
2. **Approve clone** → review the analysis.
3. **Requirements review** shows actors, requirements, acceptance criteria, API contracts, the model's *environment needs*, and the host's deterministic
   **environment plan** (prerequisites with versions and install hints, isolation strategy, exact install commands).
4. **Environment setup** is a tool-call approval: nothing is installed until you click *Install in isolated env* (or *Skip*). Libraries go into a venv,
   the project's `node_modules`, or harness cache directories — never the system interpreter. Missing system tools (JDK, Node, Maven…) are reported with
   install hints; install them and press *Re-check environment*.
5. Review **test plan**, then **test design** (cases per actor, with level, target and preconditions; unit cases are required). **Test source is generated only after you approve the design.**
6. Review the generated tests, then approve **Run the tests here** (you see every command first).
7. The **QA report** shows the verdict, per-case and per-requirement results, and **Files to update** (generated test files with line numbers, repository files
   with `file:line`, requirements still lacking a passing test). *Regenerate failing tests* sends only the failing cases back (≤3 rounds).
8. On completion, download the **ZIP** (repository + generated tests + reports).

The sidebar shows the token meter: **2,000,000 tokens per workflow run is the only limit** (no per-call or call-count limits).

## 4. Command line

```bash
uv run maf-harness run https://github.com/owner/repo                       # answer each gate in the terminal
uv run maf-harness run https://github.com/owner/repo --auto-approve --auto-heal 2      # unattended; exit 0 pass / 1 fail / 2 inconclusive
uv run maf-harness run https://github.com/owner/repo --app-url http://127.0.0.1:8080/app   # allow UI/E2E cases
uv run maf-harness verify-run .harness_runs/<run>     # evidence that MAF harness + Agent Skills were really used
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
  workspace/  environment/  executions/  generation_attempts/  audit.jsonl  run.json
```

Verdicts: `PASS` (≥1 executed passing test, no failures) · `PASS_WITH_SKIPS` · `FAIL` · `INCONCLUSIVE` (setup failed, tools missing, or zero tests ran —
never reported as pass).

## 6. Design rules the harness enforces

* **Unit tests are required** in the design when the repository has source code; every actor (persona) needs cases; every requirement needs a case or a stated reason.
* Cases carry **level, actor, target, preconditions, data**; UI/E2E/accessibility cases only when a running application URL is supplied.
* Source is generated **one file per approved case**, validated (syntax, no placeholders/skips, import resolution) before you see it.
* Skills are loaded through MAF's `SkillsProvider` and verified from a tool trace; see `verify-run`.

## 7. Development

```bash
uv run ruff check src tests app.py scripts && uv run ruff format --check src tests app.py scripts
uv run pytest -q --ignore=tests/targets                    # unit + component (real MAF) + workflow e2e; no network, no model
HARNESS_BROWSER=1 PLAYWRIGHT_BROWSERS_PATH=$PWD/.browser-cache uv run pytest tests/targets/test_harness_browser.py -q   # UI in a real browser
```

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "did not load the assigned Agent Skill" | the model skipped tool calls; retry, use a tool-capable model, or `HARNESS_SKILL_MODE=preloaded` (recorded as not using the provider) |
| Environment step says a tool is missing | install it (hint shown), then *Re-check environment* |
| `INCONCLUSIVE` with "not_executed" cases | build/collection failed; the report names the files and lines; use *Regenerate failing tests* |
| "Workflow token budget of 2,000,000 tokens exhausted" | start a new run or reduce scope; this is the only limit |
| UI tests need a live app | pass the running application URL; the harness does not start the system under test |

## 9. Security notes

Credentials are environment-only and never forwarded to install/test commands; commands are argv-only with timeouts and a plan hash checked after approval;
npm lifecycle scripts are disabled during install; generated files never touch the clone. Execution is a local process, not a sandbox — see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#4-guarantees-and-where-they-are-enforced).
