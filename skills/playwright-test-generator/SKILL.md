---
name: playwright-test-generator
version: 2.0.0
description: "Generate Playwright TypeScript UI and API automation scripts from structured test cases, fixtures, and acceptance criteria. Generate .spec.ts scripts. Trigger: playwright test generation, automation script, e2e test, api test automation"
author: AI CoE
tags: [playwright, testing, automation, generation, e2e, api-testing, structured-input]
---

# Skill: Playwright Test Script Generator (Pipeline Step 5)

## Purpose
Transform structured test-case specifications, test-data fixtures, and API contracts from upstream pipeline skills (`test-design-generator`, `test-data-generator`) into deterministic, traceable TypeScript Playwright automation scripts (.spec.ts). Support both **UI/page-based tests** and **API/request-based contract tests**.

**Do NOT use** when:
- Input is already a `.spec.ts` file (use a validation skill instead)
- The user requests a non-Playwright automation framework (e.g., Cypress, Selenium)
- The test case is manual-only with no acceptance criteria

## Pipeline Position
This skill is **Step 5** in the deterministic test-generation pipeline:
```
User Story / Acceptance Criteria
    ↓
[Step 1] Requirement Analyzer → requirements.json
    ↓
[Step 2] Test Design Generator → test_cases.json
    ↓
[Step 3] Test Data Generator → fixtures.json, test_data.json
    ↓
[Step 4] API Contract Analyzer (optional) → api_contracts.json
    ↓
[Step 5] Playwright Test Generator → *.spec.ts scripts
    ↓
[Step 6] Traceability Generator → traceability_matrix.json
    ↓
[Step 7] Coverage Analyzer → coverage_report.json
```

## Input Contract (Upstream Sources)

This skill consumes JSON artifacts from upstream pipeline skills. **All inputs must be present in the `input/` directory.**

| Input File | Source Skill | Required | Format | Description |
|---|---|---|---|---|
| `test_cases.json` | `test-design-generator` | **YES** | JSON array | Array of test-case objects with `id`, `title`, `type`, `steps[]`, `acceptance_criteria[]`, `linked_requirements[]` |
| `fixtures.json` / `test_data.json` | `test-data-generator` | NO (optional) | JSON object | Positive, negative, boundary, and API payload fixtures keyed by test case ID |
| `api_contracts.json` | `api-contract-analyzer` | NO (optional) | JSON array | API contract objects with `path`, `method`, `request_schema`, `response_schema`, `status_codes[]` |
| `user-story.md` | User / Jira / Confluence | NO | Markdown | Original user story for context (referenced in generated test comments) |
| `acceptance-criteria.md` | User / Jira / Confluence | NO | Markdown | Acceptance criteria text for cross-reference |

### Input JSON Schema (test_cases)
```json
{
  "test_cases": [
    {
      "id": "TC-001",
      "title": "User logs in with valid credentials",
      "type": "UI" | "API" | "HYBRID",
      "story_id": "US-123",
      "linked_requirements": ["REQ-001", "REQ-002"],
      "steps": [
        {
          "order": 1,
          "action": "Navigate to login page",
          "target": "https://app.example.com/login",
          "locator": "[data-test=login-form]",
          "expected": "Login form is visible"
        }
      ],
      "acceptance_criteria": [
        "User can enter email",
        "User can enter password",
        "Submit button is active when form is valid"
      ],
      "preconditions": ["User is not logged in"],
      "postconditions": ["User is authenticated and redirected to dashboard"],
      "test_data_refs": ["TC-001_positive_fixtures", "TC-001_negative_fixtures"]
    }
  ]
}
```

`steps[].locator` is optional. Supply it whenever a UI assertion or interaction needs a
specific selector; preserve it exactly in generated code. If it is absent, use an evidenced
role/label only when the input names it, otherwise emit a TODO rather than inventing one.

### Input JSON Schema (api_contracts)
```json
{
  "api_contracts": [
    {
      "path": "/api/users/login",
      "method": "POST",
      "request_schema": {
        "type": "object",
        "required": ["email", "password"],
        "properties": {
          "email": { "type": "string", "format": "email" },
          "password": { "type": "string", "minLength": 8 }
        }
      },
      "response_schema": {
        "type": "object",
        "properties": {
          "token": { "type": "string" },
          "user_id": { "type": "string" }
        }
      },
      "status_codes": [200, 400, 401, 403, 500]
    }
  ]
}
```

## Output Contract (Generated Artifacts)

All generated `.spec.ts` files must follow this deterministic structure:

| Output | Naming Convention | Description |
|---|---|---|
| UI Test Script | `<test_case_id>.spec.ts` | e.g., `TC-001.spec.ts` |
| API Test Script | `api_<test_case_id>.spec.ts` | e.g., `api_TC-001.spec.ts` |
| Hybrid Test Script | `hybrid_<test_case_id>.spec.ts` | e.g., `hybrid_TC-001.spec.ts` |

### Output File Structure (TypeScript)
Every generated `.spec.ts` file must include:

1. **Generated Metadata Comment Block** (top of file)
   ```typescript
   // ============================================
   // Playwright Test Script (Auto-Generated)
   // ============================================
   // Generated from: <test_case.id>
   // Traceability: <linked_requirements comma-separated>
   // Story: <story_id>
   // Test Type: UI | API | HYBRID
   // Timestamp: 2026-08-17T10:00:00Z
   // ============================================
   ```

2. **Test Describe Block with test case title**
   ```typescript
   import { test, expect } from '@playwright/test';

   test.describe('TC-001: User logs in with valid credentials', () => {
     test('completes the documented journey', async ({ page }) => {
       // ...
     });
   });
   ```

3. **Before/After Hooks** (setup/teardown)
4. **Test Cases** (each with clear arrange-act-assert structure)
5. **Traceability Comments** on key assertions:
   ```typescript
   // Traceability: REQ-001
   expect(await page.locator('.dashboard-title').isVisible()).toBe(true);
   ```

---

## Workflow (Plan-Execute-Validate)

### 1. Planning & Context Gathering
- **Identify Target**: Confirm the URL and the core user journey.
- **Peek at Page**: If possible, inspect the page HTML to find the most robust locators (prioritize `data-test` or `id`).
- **Handle Ambiguity**: If a step is vague (e.g., "Login"), assume standard fields but mark them for verification.

### 2. Implementation Rules
- **Selector Hierarchy (The Default)**:
    1.  `page.locator('[data-test="..."]')` (Best)
    2.  `page.getByRole()` or `page.getByLabel()` (Good, but check for technical linkage in HTML)
    3.  `page.getByPlaceholder()`
    4.  `page.locator('input[type="..."]')` (Fallback for unlinked labels)
- **Wait Management**: Use auto-waiting; never use `waitForTimeout`.
- **Assertions**: 
    - Use web-first assertions (e.g., `expect(locator).toBeVisible()`).
    - **Case Sensitivity**: For text-based assertions like `toContainText`, always use `{ ignoreCase: true }` unless specific casing is a requirement. This ensures resilience against minor UI text changes.

### 3. Validation Checklist
Before outputting the script, verify:
- [ ] Are all locators specific enough to avoid **Strict Mode Violations** (e.g., "Male" vs "FeMale")?
- [ ] Does the script handle the full flow from `goto` to the final assertion?
- [ ] Is the code clean, commented, and following the Page Object Model (if requested)?

## 🛠️ Resources & Tools
- **MCP Configuration**: `scripts/mcp.json` - Use this to initialize the Playwright MCP server for browser-based context gathering.
- **Input Datasets**: `test-data/inputs/playwright-test-generator/` - Reference these for standard testing scenarios.
- **Execution Output**: All generated scripts should be saved to the project root's `test-data/outputs/playwright-test-generator/` directory.
- **Templates**: `assets/templates.md` - Use these as base structures for generated scripts.
- **Reference Material**: `references/` - Contains testing guides and presentation materials.

## ⚠️ Gotchas
- **Strict Mode**: `getByRole('radio', { name: 'Male' })` will fail if "FeMale" is also present. **Always use `{ exact: true }` for short text matches.**
- **Unlinked Labels**: Some sites (e.g., registration forms) have labels that aren't linked to inputs via `for` attributes. If `getByLabel` fails, fall back to `page.locator('input[value="..."]')`, `type`, `name`, or `id`. Always verify locator count before clicking/checking.
- **Shadow DOM**: Standard locators might fail inside Shadow roots; use deep combinators if necessary.

## 🚫 Anti-Hallucination Rules (Critical)

**NEVER generate code without evidence from source inputs. These rules prevent fabrication:**

### For UI Tests
1. **Never invent page URLs** — Always use the `target` or `baseURL` from the test case or input config. If missing, generate a `TODO` comment: `// TODO: Confirm target URL with acceptance criteria`.
2. **Never invent selectors** — Always derive locators from acceptance criteria or fixture data. If the criterion says "click the Save button," use selector hierarchy (`data-test` > `getByRole` > fallback). If uncertain, mark as `TODO: Verify locator for "Save button"`.
3. **Never invent assertion text** — Always copy expected values from `test_cases[].acceptance_criteria` or from fixture data. If the expected text is missing, generate: `// TODO: Expected text not provided in test case`.
4. **Never skip a step** — If a test case has 5 steps and one step's target selector is missing, do NOT omit that step. Instead, generate the step with a `TODO` comment and a placeholder locator.
5. **Never assume a page structure** — If the step is ambiguous (e.g., "Enter email"), do not assume the HTML structure. Generate a comment explaining the assumption and mark it for verification.

### For API Tests
1. **Never invent endpoint paths** — Always use the exact `path` and `method` from `api_contracts[]`. Do not infer endpoints.
2. **Never invent request payloads** — Always use fixture data from `test_data.json` or `request_schema` constraints. Do not guess field values.
3. **Never invent response assertions** — Always validate against `response_schema` or `status_codes[]` from the contract. Do not assert fields that are not in the schema.
4. **Never skip status-code coverage** — Generate test cases for all `status_codes[]` listed in the contract (200, 400, 401, etc.).

### For All Tests
1. **Preserve test case IDs** — Every generated test must emit `// Generated from: <test_case.id>` in the metadata block.
2. **Preserve requirement links** — Every test must include `// Traceability: <linked_requirements>` comments on assertions that verify requirements.

---

## ⚠️ Error Handling & Gap Logging

If a test case is **incomplete or ambiguous**, **generate a placeholder test with TODO comments** rather than skipping or hallucinating.

| Scenario | Action | Example |
|---|---|---|
| Missing target URL | Generate `TODO` comment in test | `// TODO: Confirm target URL is https://...` |
| Missing selector | Generate placeholder with comment | `// TODO: Find selector for "Save button"` <br/> `page.locator('[data-test="save-button"]')` |
| Missing expected text | Generate placeholder assertion | `// TODO: Expected text not provided` <br/> `expect(...).toContainText('...')` |
| Missing fixture data | Generate a TODO-backed placeholder | Do not invent data; reference the missing fixture ID and request it |
| Conflicting requirements | Log ambiguity in comment | `// Ambiguity: Requirement says both "required" and "optional". Treating as required.` |
| Unsupported test type | Return structured error | `{ error: true, code: "UNSUPPORTED_TYPE", message: "Test type 'MOBILE' not supported. Supported: UI, API, HYBRID" }` |

**Gap Log Format** (if generating multiple tests):
```json
{
  "total_tests": 10,
  "generated": 8,
  "with_gaps": 2,
  "gaps": [
    {
      "test_id": "TC-005",
      "gap_type": "missing_selector",
      "description": "Target selector not found for 'Submit button'",
      "action": "Generated TODO placeholder. Manual verification required."
    }
  ]
}
```

---

## 🎯 Traceability & Deterministic ID Rules

1. **File Naming** — Output files must use deterministic names derived from input test case IDs:
   - UI test: `<test_case_id>.spec.ts` (e.g., `TC-001.spec.ts`)
   - API test: `api_<test_case_id>.spec.ts` (e.g., `api_TC-001.spec.ts`)
   - Hybrid test: `hybrid_<test_case_id>.spec.ts` (e.g., `hybrid_TC-001.spec.ts`)

2. **Metadata Block** — Every generated file must include at the top:
   ```typescript
   // Generated from: <test_case.id>
   // Traceability: <linked_requirements comma-separated>
   // Story: <story_id>
   ```

3. **Requirement Comments** — On every assertion that verifies a requirement, add an inline comment:
   ```typescript
   // Traceability: REQ-001
   expect(loginForm).toBeVisible();
   ```

4. **Fixture References** — When using test data from `fixtures.json`, reference it in a comment:
   ```typescript
   // Fixture: TC-001_positive_fixtures
   const testData = fixtures['TC-001_positive_fixtures'];
   ```

---

## 📋 API Test Generation Rules

When `test_cases[].type === 'API'`, generate test cases for the matching contract from `api_contracts.json`:

1. **Endpoint Matching** — Match `test_case.steps[].target` to `api_contract.path` and `api_contract.method`.
2. **Request Payload** — Use fixture data from `test_data.json[<test_case.id>_api_payloads]` or derive from `request_schema`.
3. **Status Code Coverage** — Generate separate test cases for each status code in `api_contract.status_codes[]`:
   - `200/201` — positive case (valid payload, expect success response)
   - `400` — negative case (invalid payload, expect validation error)
   - `401/403` — negative case (auth error)
   - `500` — edge case (server error)
4. **Response Validation** — Assert response structure matches `response_schema` and status code matches expected.
5. **Header & Query Param Tests** — If the contract includes optional headers or query params, generate parametrized tests or separate test cases.

### API Test Template Example
```typescript
import { test, expect } from '@playwright/test';

test.describe('POST /api/users/login', () => {
  test('should return 200 with valid credentials (TC-API-001)', async ({ request }) => {
    // Traceability: REQ-001
    // Fixture: TC-API-001_positive_fixtures
    const response = await request.post('/api/users/login', {
      data: { email: 'user@example.com', password: 'SecurePass123!' }
    });
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toHaveProperty('token');
    expect(body).toHaveProperty('user_id');
  });

  test('should return 400 with missing email (TC-API-002)', async ({ request }) => {
    // Traceability: REQ-001
    // Fixture: TC-API-002_negative_fixtures
    const response = await request.post('/api/users/login', {
      data: { password: 'SecurePass123!' }
    });
    expect(response.status()).toBe(400);
    const body = await response.json();
    expect(body.error).toContainEqual('email is required');
  });
});
```

---

## ✅ Self-Check Validation Checklist

Before delivering generated `.spec.ts` files, verify:

- [ ] **File naming**: Output files follow deterministic naming pattern (`<test_case_id>.spec.ts`)?
- [ ] **Metadata block**: File includes generated-from, traceability, and story comments at the top?
- [ ] **No invented selectors**: All locators are either from `data-test`, `getByRole`, or acceptance criteria?
- [ ] **No invented URLs**: All target URLs are from test case `steps[].target` or config?
- [ ] **Traceability comments**: Every assertion verifying a requirement has a `// Traceability: REQ-XXX` comment?
- [ ] **TypeScript valid**: Code is syntactically valid TypeScript and Playwright-compatible?
- [ ] **TODO placeholders**: Missing information is marked with `// TODO` comments instead of hallucinations?
- [ ] **Fixture references**: Test data usage is documented with fixture ID comments?
- [ ] **Error cases**: API tests include negative and boundary case test cases?
- [ ] **Auto-waiting**: No `waitForTimeout` calls; using auto-waiting and web-first assertions?

---

## 🧪 Testing Scenarios

### Positive Scenario: Standard User Journey
**Goal**: Verify a successful linear flow (e.g., login and navigate to a dashboard).
**Expected Behavior**: Agent generates a script using robust selectors and web-first assertions.
**Success Criteria**: The script runs and passes on the first try.

### Negative Scenario: Form Validation
**Goal**: Verify handling of invalid inputs and error messages.
**Expected Behavior**: Agent generates steps to input invalid data and asserts that specific error messages become visible.
**Success Criteria**: The test accurately validates the error handling logic.

### Negative Scenario: Robustness against Ambiguity
**Goal**: Handle pages with multiple similar elements.
**Expected Behavior**: Agent uses specific parent locators or unique attributes (like `id` or `data-test`) to target the correct element among duplicates.
**Success Criteria**: The script avoids strict mode violations and clicks the intended target.
