# Output structure

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

## Traceability and deterministic IDs

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
