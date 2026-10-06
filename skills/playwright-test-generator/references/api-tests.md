# API test generation rules

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
