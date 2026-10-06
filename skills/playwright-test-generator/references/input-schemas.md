# Input contract and schemas

The host supplies these artifacts (from `test-design-generator`, `test-data-generator`, `api-contract-analyzer`) in the prompt.

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
role/label only when the input names it, otherwise record a gap rather than inventing one.

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
