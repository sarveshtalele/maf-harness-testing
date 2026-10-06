# Strict test generation and evidence scope

Current `new-ui` behavior, October 6, 2026. Product: **Agentic QE Platform**.

## Why the earlier suite was too small

The host sliced deterministic facts to 30 and filtered them through the smaller model evidence sample. This cut the input to planning and design before generation. The eight-file response schema was already a **per-call** limit, not a total-run limit. Increasing that limit would not recover omitted requirements.

Requirements now retain every declaration discovered by the bounded static parser. The aggregate inventory has no 30-item cap; schema validation uses component batches of at most 30. Design processes component batches of at most 12 requirements, merges them, and validates the complete result. Accepted batches are persisted and reused on retry only when inputs and policy match. Generation processes every approved case in batches of 1–8 files (default three), with exactly one concrete test per case/file.

The current Petstore checkout has 68 production Java files and 463 public-method declaration candidates in 39 files. These are current parser measurements, not the supplied older review's 223-declaration count and not a claim of complete business requirements. Constructors, dynamic behavior and unsupported syntax still need source review. File/byte safety limits and unsupported parsers remain explicit gaps.

## Enforced rules

1. A truncated deterministic inventory blocks generation. Rerun Requirements and Test design for old runs with incomplete intake; saved evidence is preserved.
2. Every approved requirement needs a linked case or an explicit ID-linked exclusion reason. Unexplained omissions remain errors on the final repair attempt.
3. Java unit-method claims need a direct invocation on a receiver declared with the source type. Constructor calls, imports and comments do not cover setters. `EqualsVerifier.forClass` is recognized for equals/hashCode.
4. Literal positive Java fixtures must respect source `@Size` constraints when supplied to constructors or setters. Invalid input belongs in a negative/validation case with an actual rejection assertion. Indirect values and complex annotation expressions still require runtime validation and review.
5. Constant boolean assertions and exact duplicate existing Java test bodies are rejected. Existing relevant test excerpts are included in design and generation. This duplicate check is conservative token comparison, not semantic equivalence.
6. Existing source, dependency pins and framework conventions stay authoritative. Petstore uses JUnit 4; a JUnit 5 scaffold must not override its build.
7. Compilation, collection, dependency installation, application startup and execution remain deferred until their Test execution approvals. Earlier phases read source and validate generated text.

Direct invocation is a static witness, not proof that the assertion fully captures behavior. The host does not certify arbitrary Java semantics, mocked boundaries or model-authored expectations.

## Independent review, import repair and stopping

After the shared source/import/path gates, a separate Repository Test Reviewer persona reviews every generated case against its approved design, linked requirements and repository source. It assigns a 0–100 confidence judgment with a reason and source citations. Unsupported or insufficiently evidenced cases return to the bounded generation repair loop. Accepted batches are retained; retries do not discard previously accepted work. Generated tests and QA case tables display accepted reviews in descending confidence order. These judgments are not probabilities or execution results.

Invalid reviewer IDs, schema or citations receive bounded reviewer-only repair with the unchanged generated candidate. Citations must identify existing implementation files, not proposed test paths. Runtime healing supplies the exact failed generated source together with execution differences; passing files and their accepted reviews remain unchanged. This reduces repeated reconstruction of the same defective test. A model can still produce a wrong expectation or review: a failed execution stays FAIL and a repair limit returns a retry/review/stop gate.

For Python, an invented dotted import is still rejected. Repair feedback suggests verified repository paths. A directory such as `skills/text-statistics` cannot be renamed to `skills.text_statistics` by an import statement; tests can load its actual file with `importlib.util.spec_from_file_location`, using the repository working directory and verified callable signature. Source inspection and static checks do not execute that file before Test execution.

The active **Stop run** button cancels model work and signals owned clone/setup/test command processes to terminate. Saved analysis, rejected reviews, logs and earlier artifacts remain available. A currently blocking filesystem operation may finish before cancellation is observed; stopping is not a rollback of already approved installation. At a waiting approval, **Stop workflow** preserves the same evidence. Reviewer prompts are saved with the `test_reviewer` role alongside author prompts.

## Execution and QA

Maven plans separate three scopes:

| Scope | Evidence |
|---|---|
| Existing unit regression | Discovered repository unit-test classes, selected through Surefire |
| Existing integration regression | Discovered integration classes; the supported repository-managed Arquillian/WildFly profile, selected through Failsafe |
| Generated cases | Approved generated classes only, mapped to stable case IDs |

Zero fresh evidence, unsupported integration configuration and unexecuted approved cases cannot become PASS. Baseline results do not become unmapped generated cases. Baseline failures affect the overall suite verdict; generated results retain their own verdict. QA displays source intake, baseline counts/reasons and exact failure output separately. Requirement traceability is not line, branch, mutation or full application coverage. Those runtime measurements are explicitly **not measured** until actual tool reports are integrated; no coverage percentage is invented.

Petstore's managed profile also contains an upstream WildFly unpack-directory / `jboss.home` mismatch. A failure in that configuration is repository/environment evidence, not proof that the generated application tests failed. The harness does not silently alter the target POM or weaken assertions.

## Local use and debugging

```powershell
# Windows: run from the downloaded checkout
.\start-windows.cmd
```

```bash
# macOS
./start-macos.command
# Linux
bash start-linux.sh
```

Node.js 24 runs Next.js 16; these are separate versions. The default run directory is `<repo-root>/.maf-harness/runs/<run-id>` and service logs are `<repo-root>/.maf-harness/logs`. Both are created automatically and ignored by Git. Explicit `HARNESS_RUNS_DIR` overrides remain supported. Old profile and `.harness_runs` history remains readable in place.

In the UI, open any saved run → Review/Artifacts for earlier analysis, QA report for scoped results, Logs for saved command output, and Observability/Audit for calls, skills, retries and approvals. Failed attempts remain evidence; completion is not a test verdict.

To reproduce the strict checks without a gateway:

```bash
uv sync --extra dev
uv run pytest tests/test_petstore_quality.py tests/test_deterministic_analysis.py tests/test_generation_gates.py tests/test_stack_and_runners.py -q
uv run ruff check src tests scripts
uv run maf-harness lint-skills
```

For an explicitly approved live clone/install/execution cycle:

```bash
uv run maf-harness run https://github.com/agoncal/agoncal-application-petstore-ee7 --auto-approve --max-retries 1
```

Configure the ignored `.env` first; never paste keys into reports or commit them. `--auto-approve` consents to the displayed workflow actions on this machine. A Java EE deployment needs the repository's actual server configuration; an empty report or undeployed UI cannot be reported as full application qualification.
