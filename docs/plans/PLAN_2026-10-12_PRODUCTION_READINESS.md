# Production readiness plan (2026-10-12)

Owner request (2026-10-12). Opus plans, reviews and merges. Haiku implements in worktrees. Sonnet reviews each branch. Opus does the final review. Every item ships with tests, and CI must pass on Windows, macOS and Linux before release.

Status key: `[ ]` open, `[~]` in progress, `[x]` done (with commit).

## 0. Root cause of the requirement-analysis-skill run (report review)

The QA report for `requirement-analysis-skill` (commit `3eae93ae5b5f`) showed 13/13 cases "verified at confidence 100", yet 2 failed at execution.

- **CTL-003, `LocationResolver.resolve`:** the target filters out zero-score candidates, so 1 result is correct. The generated test expected 2. This is a harness defect: the reviewer accepted an expectation that contradicts the source.
- **CTL-001, `CodebaseIndexer.build`:** the target computes `line_count` as `content.count("\n") + 1`, so a file with a trailing newline counts one extra line. The generated test assumed 2. This is a harness defect: the expectation was not derived from the source. The target's line-count contract is a separate, possible ambiguity in the target repository.
- **Conclusion:** the report was accurate about execution, but its confidence claim was not. "Confidence 100" meant the reviewer accepted the test, not that the expectation was proven.

## 1. Accuracy and confidence (backend)

- [ ] **1.1** Add regression fixtures for CTL-001 and CTL-003 to the accuracy-gate tests. They are scripted transport with the real source, and each must be rejected before execution.
- [ ] **1.2** Source-backed expected-value verification in `workflow_steps/test_review.py`. For each assertion literal, the reviewer must show the derivation from `target_source`. A missing or contradicted derivation caps the score.
- [ ] **1.3** Deterministic checks where safe, for example collection-length and literal-count expectations against fixture data the test builds itself. A failed check caps the score.
- [ ] **1.4** Confidence agent for **test planning** and **test design**: a separate reviewer persona with its own prompt, editable in the UI (Agent prompts).
- [ ] **1.5** Gate at **95** (95 to 100 required) before evaluation and generation. Apply it to plan, design and generated-test review. It stays configurable, and it is shown in the UI and the report.
- [ ] **1.6** Full confidence traceability. Every score records:
  - the control ID and case or plan area;
  - the rubric points;
  - the caps applied, with their reasons;
  - the reviewer verdict and the evidence quotes;
  - the prompt version or hash, the model and the timestamp;
  - the round number.

  It is shown in the UI and the report, and exported.
- [ ] **1.7** The report separates **source-review accepted** from **execution passed**. "Verified" never implies runtime correctness.

## 2. UI: pages instead of pop-ups, clean template

- [ ] **2.1** A shared page template: header (title, ID, breadcrumbs, back), metadata strip, sections, actions. Every detail view and output renders through it.
- [ ] **2.2** The expand arrow (⤢) opens a dedicated page, not a modal. Record pages show ID and **Description** (renamed from "text").
- [ ] **2.3** Report flow: Objective, Recommended frameworks and the other sections become collapsible subsections, expanded on demand rather than all at once.
- [ ] **2.4** The UI never shows raw Markdown. The QA report and execution logs render as structured UI, and Markdown or JSON are offered as downloads.
- [ ] **2.5** Fix text and element collisions (overlap or clipping) on every page at 1366, 1024 and 390 px, in light and dark themes, including Windows font metrics.

## 3. MCP

- [ ] **3.1** A visible **Delete server** action, with confirmation.
- [ ] **3.2** Clicking a server card opens a dedicated **tools page**, in the style of Claude Desktop. It lists every tool with its description, grouped read-only and write. Each tool has an access control (Always allow / Ask / Blocked), with Approve all and Block all, search, and a stage budget warning.

## 4. Full project review (Opus, read-only), then fixes

- [ ] **4.1** Backend workflow and accuracy pipeline review.
- [ ] **4.2** Security review: API, auth, CORS and CSP, secrets, MCP, skills, subprocess, path traversal, Electron.
- [ ] **4.3** Frontend UI/UX, accessibility and consistency review.
- [ ] **4.4** Desktop, packaging, CI and documentation consistency review.
- [ ] **4.5** Fix every confirmed finding (Haiku), with Sonnet review.
- [ ] **4.6** Final Opus review of the merged result against this checklist.

## 5. Developer documentation

- [ ] **5.1** Complete developer guide:
  - branch per component and branch naming;
  - commit and PR conventions;
  - code naming conventions (Python, TypeScript, CSS, tests, files);
  - coding standards and principles;
  - the review and merge gate;
  - cross-OS rules;
  - security rules;
  - how to add a stage, skill, MCP preset, prompt or UI page (the template).

## 6. Release

- [ ] **6.1** Full gate: pytest, ruff, map, lint-skills, typecheck, build, Playwright, desktop tests.
- [ ] **6.2** CI on all three OSes, then an installer build with smoke tests, then a release.

## 7. Accuracy v2: accurate results for any repository (same architecture)

The owner wants the harness to test what a repository is FOR, not only that its functions execute. The stages stay the same; each gains the steps below.

- [ ] **7.1 Two requirement tiers.** **Product requirements** come from README, SKILL.md, docs, references, CLI help and examples, and each quotes its evidence. **Component requirements** are today's callable-level ones. Planning covers product requirements first.
- [ ] **7.2 Expected-value source on every case:** `specified` (doc quote), `derived` (source file:line), `metamorphic` (relation), `golden` (scenario fixture with an independently derived, human-approved answer) or `property`. A case without a source is rejected. Current-behaviour-only cases are labelled `characterization`, never "verified".
- [ ] **7.3 Blind derivation plus deterministic checks** (Opus backend review, section A):
  - the host masks assertion literals;
  - the reviewer derives each value with verbatim source and fixture quotes;
  - D1 `count_assumes_no_filter` and D2 pure-expression evaluation run with no exec;
  - D3 records a `review_miss` when a case passed review and then failed at run time;
  - the 85% anchor is removed, and the reviewer gets its own reviewer skill.
- [ ] **7.4 Verification run before "verified".** Generated tests run once in the isolated environment, behind an approval gate. Each failure is sorted, with evidence, into a product defect (the docs state the value) or a test defect (the value was guessed; the test is regenerated).
- [ ] **7.5 Confidence from evidence, gate at 95**, for planning, design and test review. Rubrics per kind, a runtime threshold resolver, editable reviewer prompts and a `confidence.jsonl` ledger (backend review, section D).
- [ ] **7.6 Measured coverage**: line and branch coverage (coverage.py, then c8 and JaCoCo). Mutation testing is optional.
- [ ] **7.7 Report split**: product behaviour, component, characterization. Failures are split into product defects and repaired test defects, and "review accepted" is kept separate from "execution passed".
- [ ] **7.8 Harness accuracy benchmark**: seeded-bug variants of requirement-analysis-skill. Measure how many seeded bugs the harness detects and how often it fails a test that should pass; the false-fail rate must be 0 after triage.
- [ ] **7.9 End-to-end run on requirement-analysis-skill** with the new pipeline; compare against the 2026-10-12 report.

Review findings: `.watch/review-2026-10-12/{backend,frontend,desktop-ci-docs,security}.md` (outside the repository).
