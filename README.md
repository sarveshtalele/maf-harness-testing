# MAF Test Harness

A human-approved, evidence-driven **test-engineering harness** on [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/) (MAF).
Give it a public GitHub repository; it analyzes the code, infers requirements (with actors/personas), plans and
designs tests, generates them in the repository's native framework, **runs them**, and produces a QA report that
traces every requirement to an approved case, a generated test, and an executed result.

```text
repo URL ─► clone + analyze ─► requirements ─► test plan ─► test design ─► test generation ─► run ─► QA report
             (host)            (agent+skill)   (agent+skill) (agent+skill)  (agent+skill)     (host)  (host)
                  every arrow is a human gate · running code needs its own approval · failing tests can be regenerated
```

* **Real MAF, verified.** Each model stage is a `create_harness_agent` with a **`SkillsProvider`** (MS Learn *Agent Skills*,
  progressive disclosure). The host verifies from a `FunctionMiddleware` trace that the assigned skill was really loaded through
  MAF's `load_skill` tool; otherwise the stage's output is rejected. Orchestration is a checkpointed MAF `WorkflowBuilder`.
* **Any stack.** Deterministic stack profile + runner adapters for Python/pytest, Node (jest, vitest, Playwright), Java
  (Maven, Gradle), Go, .NET. The model picks tests; the *host* picks and shows every command.
* **Honest verdicts.** `PASS` needs fresh report evidence with ≥1 passing test and no failures. Setup failure, missing tools,
  zero tests → `INCONCLUSIVE`. Skips → `PASS_WITH_SKIPS`.

See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) (design decisions, MS Learn alignment, roadmap, risks),
[docs/audit/ERRORS.md](docs/audit/ERRORS.md) (defect table) and [docs/audit/E2E_RESULTS.md](docs/audit/E2E_RESULTS.md)
(live end-to-end runs on real repositories).

## Quick start

```bash
uv sync --extra dev                      # add --extra e2e for Playwright UI tests
export OPENAI_API_KEY=...                # never commit; see .env.example
export OPENAI_BASE_URL=https://your-openai-compatible-gateway   # optional
export OPENAI_MODEL=deepseek-v4-flash-global                    # any tool-capable chat model

uv run streamlit run app.py              # UI
uv run maf-harness run https://github.com/owner/repo            # terminal, answer each gate
uv run maf-harness run https://github.com/owner/repo --auto-approve --auto-heal 2   # CI; exit 0/1/2
uv run maf-harness lint-skills           # Agent Skills spec lint
```

`--auto-approve` is your explicit consent to clone the repository and run its generated tests **on this machine as your
user** (a local process, *not* a sandbox). Use a disposable environment for untrusted repositories.

### Repository URL policy

Both forms are accepted and normalized to one HTTPS clone URL: the **GitHub page URL** (`https://github.com/owner/repo`,
optionally `/tree/<branch-or-tag>` to pin a ref) and the **clone URL** (`…/repo.git`). SSH, other hosts, credentials,
query strings and non-repository pages are rejected. Clone never prompts for credentials (private repos fail fast).

### Testing UI/E2E for a running application

Pass `--app-url http://localhost:8080` (UI field "Running application URL"). Only then may the design contain
`ui`/`e2e`/`accessibility` cases; generated tests read the target from the `BASE_URL` environment variable. Start the
application yourself (an application lifecycle manager is on the roadmap).

## What each stage produces

| Stage | Persona | Skill (loaded via MAF `load_skill`) | Output |
|---|---|---|---|
| Repository analysis | Repository Intelligence Analyst | host (deterministic) | languages, frameworks, tests, stack profile, evidence |
| Requirements | Repository Requirements Analyst | `repository-requirements` | actors, requirements (+category/actor), acceptance criteria, validation rules, explicit API contracts, gaps |
| Test planning | Principal Test Architect | `test-planning` | risk-based areas by level, frameworks, setup, order |
| Test design | Senior Test Design Engineer | `test-design-generator` | cases with **level, actor, target, preconditions, data**, priority, traceability matrix, coverage, gaps |
| Test generation | Polyglot Test Automation Engineer | `generic-test-generator` | one file per approved case, validated statically |
| Test execution & QA | QA Execution Engineer (host, no model) | — | adapter plan → run → case/requirement results, verdict |

Design rules enforced by the host: unknown actors/levels are rejected; UI/E2E/accessibility levels need a running app URL;
contract tests only for explicit API contracts; every case links to requirement/criterion/rule/contract IDs; IDs are
stable and human-readable (`TC-POS-CLI-HELP-EXITS-0-A1B2C3D4`).

## Determinism: what is and is not guaranteed

| Deterministic (host) | Not deterministic (model) |
|---|---|
| URL normalization, stage → skill assignment, skill package hashes, verification that `load_skill` ran, IDs, schema and traceability validation, generated-path safety, runner choice and commands, evidence-based verdict, audit log | requirement wording, test design content, generated source, gateway behavior, timing. `temperature=0` reduces variance; it does not remove it. |

Skill modes (`HARNESS_SKILL_MODE`): `progressive` (default; real MAF `SkillsProvider`, host-verified) or `preloaded`
(skill text injected into instructions for models that cannot call tools — recorded as *not* using the provider).

## Security model

* Credentials are environment-only, never stored, and **never forwarded** to setup/test commands (allow-listed env, fake `HOME`).
* Cloning and running tests are separate explicit approvals; the exact argv list and a plan hash are shown first.
* Execution is argv-only (no shell), timeout kills the process group, every run writes a new immutable result directory.
* Generated tests are copied with the repository into a **new workspace**; the clone is never modified.
* Skills are treated as third-party code: spec-linted, hashed, scripts disabled, only read-only skill tools auto-approved.
* Repository text is untrusted evidence; secrets files are excluded and inline secrets redacted before prompts.
* **Not a sandbox.** Container/VM isolation is the top roadmap item and a release gate for unattended use.

## Layout

```
src/maf_test_harness/  repo_analyzer stack maf_runtime skills_registry personas models skill_contracts
                       workflow runners execution qa_report cli
app.py                 Streamlit UI            skills/   bundled Agent Skills (8)
tests/                 unit · component (real MAF) · workflow e2e · targets/ (opt-in live browsers/apps)
docs/                  IMPLEMENTATION_PLAN · audit/ (ERRORS, E2E_RESULTS, evidence)   patches/  fixes for target-app defects
```

## Development

```bash
uv run ruff check src tests app.py scripts
uv run pytest -q --ignore=tests/targets           # unit + component + workflow e2e (no network, no model)
HARNESS_BROWSER=1 uv run pytest tests/targets/test_harness_browser.py -q   # Streamlit UI in a real browser
REVERSE_ENGINEER_REPO=/path/to/checkout uv run pytest tests/targets/test_reverse_engineering.py -q
```

Live end-to-end (uses a real gateway; costs tokens): see [docs/audit/E2E_RESULTS.md](docs/audit/E2E_RESULTS.md) for the exact commands.

## Known limitations

Local-process execution only; no application lifecycle manager; static validation is deep for Python and best-effort for
other languages (compile gates are on the roadmap); evidence is bounded excerpts, not full-repo retrieval; the model can
still author wrong tests, which is why design and source are human-reviewed and a passing verdict never claims correctness.
