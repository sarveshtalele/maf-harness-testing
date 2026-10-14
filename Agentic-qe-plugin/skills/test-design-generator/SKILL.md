---
name: test-design-generator
description: Design deterministic, source-traced E2E case records from approved requirements and plans.
---

# E2E case design

Adapted from MAF `skills/test-design-generator`. Its generated output here is a **case catalog**, never runnable test source. Require stable approved story, acceptance criterion, requirement, control and plan IDs. If the required model is missing or malformed, record the exact blocker instead of producing a partial success.

1. Design at least one positive journey per explicit acceptance criterion, or record an ID-linked gap.
2. Design a negative or validation journey only for an explicit rule. Derive boundary variants only from an explicit numeric, length, date or range limit. For API paths, use only supplied contract methods and responses.
3. Give each `TC-*` a unique stable ID, title, precondition when needed, ordered action/expected steps, final observable outcome, `level: "e2e"`, source refs, and exact requirement/control/plan links. Use user or external-system boundaries for technical constraints.
4. Use P0 only for source-identified release, security, payment or data-loss impact. Otherwise use P1 for core behavior and note that default; use P2 for lower impact.
5. Reconcile every supplied obligation to a case or explicit gap. Do not use a matching title, existing repository test, placeholder, unsupported expected error or invented endpoint as coverage.

Check unique IDs and all foreign keys using `qe.py validate`. Never write `.spec.ts`, JUnit or other executable tests.
