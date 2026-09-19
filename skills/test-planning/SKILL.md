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
Create a test plan from the repository-analysis JSON and bounded source evidence supplied by the host. This skill plans testing only; it does not write test code or invent application behavior.

## Rules
1. Treat repository-analysis facts as authoritative for languages, frameworks, package files, and current test inventory.
2. Recommend frameworks already present when possible. If no test framework exists, choose a mainstream framework compatible with the detected stack and mark that choice as a recommendation.
3. Prioritize observable business logic, API boundaries, data access, error handling, authentication/authorization, and critical UI flows only when source evidence supports them.
4. Every planned area must cite one or more evidence file paths whenever such files are available.
5. Put uncertainty, missing requirements, and unverified behavior in `gaps`; never convert uncertainty into a requirement.
6. Keep the plan implementable: include setup actions and an ordered execution sequence.
7. Do not generate individual test cases in this stage.

## Output
Return exactly one JSON object matching the schema supplied by the host application. Do not wrap it in Markdown fences and do not create files.
