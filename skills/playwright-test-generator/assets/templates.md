# Playwright Test Templates

Use these templates as a baseline when generating scripts to ensure consistency across the project.

## 1. Standard Functional Test
Use this for simple, linear user journeys.

```typescript
import { test, expect } from '@playwright/test';

test('describe the test scenario here', async ({ page }) => {
  // 1. Navigate to the target URL
  await page.goto('https://example.com');

  // 2. Perform actions using robust locators (prioritize data-test)
  await page.locator('[data-test="username"]').fill('user_name');
  await page.locator('[data-test="login-button"]').click();

  // 3. Web-first assertions
  await expect(page.locator('.welcome-message')).toBeVisible();
});
```

## 2. Page Object Model (POM) Template
Use this for more complex suites to improve maintainability.

### Page Class (`login.page.ts`)
```typescript
import { Page, Locator } from '@playwright/test';

export class LoginPage {
  readonly page: Page;
  readonly usernameInput: Locator;
  readonly loginButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.usernameInput = page.locator('[data-test="username"]');
    this.loginButton = page.locator('[data-test="login-button"]');
  }

  async login(user: string) {
    await this.usernameInput.fill(user);
    await this.loginButton.click();
  }
}
```

### Test File (`login.spec.ts`)
```typescript
import { test, expect } from '@playwright/test';
import { LoginPage } from './login.page';

test('successful login via POM', async ({ page }) => {
  const loginPage = new LoginPage(page);
  await page.goto('/login');
  await loginPage.login('standard_user');
  await expect(page).toHaveURL(/inventory/);
});
```

## 3. Data-Driven Test Template
Use this when testing multiple inputs for the same flow.

```typescript
import { test, expect } from '@playwright/test';

const testCases = [
  { user: 'standard_user', expectedUrl: /inventory/ },
  { user: 'locked_out_user', expectedUrl: /login/ },
];

for (const data of testCases) {
  test(`Login test for ${data.user}`, async ({ page }) => {
    await page.goto('https://www.saucedemo.com/');
    await page.locator('[data-test="username"]').fill(data.user);
    await page.locator('[data-test="password"]').fill('secret_sauce');
    await page.locator('[data-test="login-button"]').click();
    await expect(page).toHaveURL(data.expectedUrl);
  });
}
```

## 4. API Contract Test Template
Use this when the input test case is `API`. Use only the supplied contract path, method,
fixtures, and response schema fields.

```typescript
import { test, expect } from '@playwright/test';

test.describe('<METHOD> <contract.path>', () => {
  test('<test_case.id>: returns <status> for documented payload', async ({ request }) => {
    // Fixture: <fixture_id>
    const response = await request.<method>('<contract.path>', { data: fixtureData });

    // Traceability: <requirement_id>
    expect(response.status()).toBe(<status>);
    const body = await response.json();
    expect(body).toHaveProperty('<response_schema.property>');
  });
});
```

## 5. Hybrid Test Template
Use this only when a test case explicitly combines documented UI actions and an API contract.

```typescript
import { test, expect } from '@playwright/test';

test('<test_case.id>: <title>', async ({ page, request }) => {
  // Fixture: <fixture_id>
  const response = await request.<method>('<contract.path>', { data: fixtureData });
  expect(response.status()).toBe(<status>);

  await page.goto('<documented URL>');
  // Traceability: <requirement_id>
  await expect(page.locator('<documented locator>')).toBeVisible();
});
```

## 6. Metadata and Traceability Template
Place this block at the top of every generated file. Add a traceability comment immediately
before each assertion that verifies a linked requirement.

```typescript
// ============================================
// Playwright Test Script (Auto-Generated)
// ============================================
// Generated from: <test_case.id>
// Traceability: <linked_requirements comma-separated>
// Story: <story_id>
// Test Type: UI | API | HYBRID
// ============================================

// Traceability: <requirement_id>
await expect(locator).toBeVisible();
```
