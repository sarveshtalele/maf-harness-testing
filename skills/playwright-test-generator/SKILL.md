---
name: playwright-test-generator
description: "Generate Playwright TypeScript UI and API automation (.spec.ts) from structured test cases, fixtures and API contracts. Use for approved ui/e2e/api cases when Playwright is the browser framework; not for existing .spec.ts files, Cypress/Selenium, or manual-only cases."
metadata:
  version: "2.1.0"
  tags: playwright testing automation e2e api-testing
---

# Playwright Test Generator

## Goal
Turn approved test cases (plus optional fixtures and API contracts) into deterministic, traceable Playwright TypeScript `.spec.ts` tests, for both UI/page-based and API/request-based cases. Use only when the repository has no other browser E2E framework or already uses Playwright.

## Inputs
- Test cases: `id`, `title`, `type` (UI | API | HYBRID), `steps[]`, `acceptance_criteria[]`, `linked_requirements[]`.
- Optional fixtures keyed by test case ID, optional API contracts (`path`, `method`, schemas, `status_codes[]`), and the base URL from the `BASE_URL` environment variable.
- Schemas and an example: `references/input-schemas.md`, `examples/`.

## Steps
1. Read `references/input-schemas.md` and confirm every case has an ID and steps; a missing input is a reported gap, never a guess.
2. Identify the target: the case `target`/route or `BASE_URL`. Do not invent URLs, selectors, payloads or expected text (see `references/gaps-and-anti-hallucination.md`).
3. Choose locators in this order: `data-test`, `getByRole`/`getByLabel` (with `{ exact: true }` for short text), `getByPlaceholder`, attribute fallback.
4. Use auto-waiting and web-first assertions (`expect(locator).toBeVisible()`); never `waitForTimeout`. Use `{ ignoreCase: true }` for text assertions unless casing is required.
5. For API cases follow `references/api-tests.md`: exact contract path and method, fixture payloads, assertions only on documented fields and status codes.
6. Preserve traceability: metadata block, requirement comments and fixture references as in `references/output-and-traceability.md`.
7. Where evidence is missing, report an explicit gap; do not emit TODOs, placeholder locators or empty assertions.
8. Do not run Playwright, install dependencies or edit files; the host validates and executes after approval.

## Output contract
One `.spec.ts` per case, named `<test_case_id>.spec.ts`, `api_<id>.spec.ts` or `hybrid_<id>.spec.ts`, containing the metadata comment block, an import of `test`/`expect` from `@playwright/test`, a `test.describe` titled with the case ID and title, and assertions on observable results. Details and templates: `references/output-and-traceability.md`, `assets/templates.md`.

## Self-check
- File names follow the deterministic pattern and the metadata block names the case and requirement IDs.
- Every locator, URL, payload and assertion value is traceable to supplied evidence.
- No `waitForTimeout`, TODO, placeholder or tautological assertion.
- API tests assert only contract-documented fields and status codes.
- The TypeScript is syntactically valid and Playwright-compatible.
