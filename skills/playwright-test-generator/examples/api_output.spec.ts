import { test, expect } from '@playwright/test';

// ============================================
// Playwright Test Script (Auto-Generated)
// ============================================
// Generated from: TC-API-LOGIN-001
// Traceability: REQ-LOGIN-01
// Story: US-LOGIN-01
// Test Type: API
// ============================================

test('TC-API-LOGIN-001: Login endpoint accepts valid credentials', async ({ request }) => {
  // Fixture: TC-API-LOGIN-001_positive_fixtures
  const response = await request.post('/api/users/login', {
    data: { email: 'member@example.test', password: 'ExamplePass123!' },
  });

  // Traceability: REQ-LOGIN-01
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body).toHaveProperty('token');
});
