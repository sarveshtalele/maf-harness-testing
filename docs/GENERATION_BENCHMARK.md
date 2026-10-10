# Generation quality benchmark

This initial benchmark scores submitted Python suites against source-backed synthetic targets and
seeded defects. It runs offline after dependencies are installed; it does not invoke a model or
change the existing generation workflow.

## Corpus

| Task | Source contract | Seeded defects |
| --- | --- | --- |
| transcript | Nested message.usage, totals from components, missing counts | Read root payload; trust invented total |
| sqlite | Explicit database path, real schema, uniqueness, rollback, disposable fixtures | Ignore duplicates; invent summary field |
| csv | Parse exported rows and accept UTF-8 charset metadata | Omit exported rows |

The three tasks, seven reference tests and five mutations reproduce patterns described in
[the fresh-cycle audit](audit/TOKEN_TELEMETRY_FRESH_2026-10-02.md). They are small synthetic
fixtures, not copies of TokenTelemetry. They do not cover async fixtures, browser selectors,
Java providers, multi-service startup or full repository qualification.

## Reference calibration

```bash
uv sync --python 3.12 --extra dev
uv run python scripts/benchmark_generation.py
uv run pytest tests/test_generation_benchmark.py -q
```

The command prints a retained report.json path and preserves per-trial stdout, stderr and JUnit.
Specify --output with a new directory to keep evidence elsewhere. Calibration must validate all
three tasks and detect all five defects. CI runs it. This is scorer calibration, **not measured
model quality**.

## Submitted candidates

Give a generator only each task's target.py, PROMPT.md and the stated available dependencies.
Keep reference suites, mutations and defect descriptions out of generation inputs. Freeze the
candidate before scoring. Supply only test_*.py files and optional conftest.py, importing target.

```text
candidate-suites/
  transcript/test_generated.py
  sqlite/test_generated.py
  sqlite/conftest.py
  csv/test_generated.py
```

```bash
uv run python scripts/benchmark_generation.py --candidate-dir /path/to/candidate-suites --output /path/to/new-evaluation
```

Each trial gets a new workspace and fresh target.py. The runner supplies PYTHONPATH, disables
third-party pytest plugin autoload, and uses the harness's sanitized process runner with a
30-second timeout. The dependency contract is Python stdlib and pytest. The runner intentionally
does not pass model credentials, but executes candidate code as the OS user. Use a disposable
machine for untrusted suites. This is not a security sandbox or an adversarial grader; tests
must not forge reports, inspect mutant files or alter targets.

Output directories must not already exist. Exit 0 requires valid baselines for all tasks and
detection of all five defects; exit 1 means a quality threshold miss.

## Metrics

- Baseline validity: fraction of tasks with at least one passed test, exit 0, and no failures,
  errors or skips on the correct target. Fixture/import/collection failures invalidate a task.
- Effective mutation score: detected defects divided by all five seeded defects. Missing or
  invalid candidate suites stay in the denominator and receive no defect-detection credit.
- Detected defect: a mutant exits 1 with a test-phase failure, no setup/collection errors or
  skips, and the same test count and identities as baseline. Crashes and timeouts do not receive credit.

A trivial suite can have perfect baseline validity and zero mutation score. Broken fixtures
cannot earn credit merely by failing on every mutant. Reports include corpus and candidate
hashes, Python version and raw process outcomes. Review assertions and task coverage separately;
these metrics do not prove arbitrary semantic correctness.

## Evaluate the actual generator next

Generate candidates through the existing harness with the same source/prompt inputs, then score
the saved files. No live-generation adapter or model automation is added in this first version.
Keep execution approvals and host validation intact.

For comparisons record harness commit, corpus hash, provider/model identifier, prompt and skill
hashes, configuration/seed where supported, original candidate files and run audit. Record
static-gate rejection rate, retries, elapsed generation time and provider-reported tokens/cost
separately; the offline scorer cannot infer them. Compare identical inputs across several
independent generation attempts. Include failed generation attempts as missing candidates.
Scoring feedback used for repair creates a new attempt: retain initial and repaired scores.

Add reviewed async-fixture and component-scoped browser tasks before claiming improvements for
those failures. Keep real-repository live audits separate from this synthetic evaluation.
