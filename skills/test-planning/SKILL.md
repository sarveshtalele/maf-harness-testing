---
name: test-planning
description: Build an evidence-based test strategy for a repository after deterministic analysis shows tests are missing. Use before test design to choose test levels, frameworks, setup, risks, execution order, assumptions, and explicit gaps.
compatibility: Python host using Microsoft Agent Framework Agent Skills.
metadata:
  sdlc: Testing
  source-inspiration: awesome-agent-skills marketplace branch
---

# Test Planning

## Goal
Create a test plan from the repository-analysis JSON and bounded source evidence supplied by the host. This skill plans testing only; it does not write test code, individual test cases, or invent application behavior.

## Inputs
Repository analysis (languages, frameworks, package files, current test inventory), approved requirements, bounded source evidence, and an optional testability analysis.

## Steps
1. Treat repository-analysis facts as authoritative for languages, frameworks, package files, and current test inventory.
2. Recommend frameworks already present. If none exists, choose a mainstream framework compatible with the detected stack and mark it as a recommendation.
3. Prioritize observable business logic, API boundaries, data access, error handling, authentication/authorization, and critical UI flows only where source evidence supports them.
4. If a testability analysis is supplied, plan low-testability targets as integration tests or explicit testability gaps.
5. Cite one or more evidence file paths for every planned area whenever such files exist.
6. Put uncertainty, missing requirements, and unverified behavior in `gaps`; never convert uncertainty into a requirement.
7. Include setup actions and an ordered execution sequence so the plan is implementable.

## Output contract
Exactly one JSON object matching the schema supplied by the host. No Markdown fences; do not create files.

## Self-check
- Every planned area cites evidence paths or a gap explains why none exist.
- Every framework is either detected or flagged as a recommendation.
- No individual test case or invented behavior appears in the plan.
