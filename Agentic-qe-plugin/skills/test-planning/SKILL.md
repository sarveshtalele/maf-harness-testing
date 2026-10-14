---
name: test-planning
description: Plan evidence-backed end-to-end journeys across all approved controls and FR/TR items.
---

# E2E test planning

Adapted from MAF `skills/test-planning`; this plugin stops at case generation. Read approved stories, requirements, controls and bounded scope. For each supported control or FR/TR item, plan a user or external-system journey with an observable outcome. Add `PLN-*` items with exact `source_refs`, requirement IDs and control IDs. Group repeated paths only when one journey still verifies every linked obligation.

Prioritize an explicitly critical flow as P0; use P1 for ordinary core behavior and P2 for lower impact. Record a default priority when the source does not state criticality. Include positive, negative, boundary and accessibility paths only when their conditions are supported. Put ambiguous or untestable obligations in `open_questions` with their IDs.

Do not select a test framework, setup commands, unit/integration levels or a test execution order. Check that every approved control and requirement appears in a plan item or an explicit blocker.
