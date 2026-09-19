import { test, expect } from '@playwright/test';

// ============================================
// Playwright Test Script (Auto-Generated)
// ============================================
// Generated from: TC-LOGIN-001
// Traceability: REQ-LOGIN-01
// Story: US-LOGIN-01
// Test Type: UI
// ============================================

const testData = {
  email: 'member@example.test',
  password: 'ExamplePass123!',
};

test.describe('TC-LOGIN-001: User logs in with valid credentials', () => {
  test('redirects a valid user to the dashboard', async ({ page }) => {
    // Fixture: TC-LOGIN-001_positive_fixtures
    await page.goto('https://app.example.test/login');
    await expect(page.locator('[data-test=login-form]')).toBeVisible();

    await page.locator('[data-test=email]').fill(testData.email);
    await page.locator('[data-test=password]').fill(testData.password);
    await page.locator('[data-test=login-submit]').click();

    // Traceability: REQ-LOGIN-01
    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
  });
});
