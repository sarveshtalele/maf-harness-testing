# Anti-hallucination, gaps and scenarios

## Anti-hallucination rules

Never generate code without evidence from the supplied inputs.

### UI tests
1. Never invent page URLs: use the case `target`, or read the base URL from the `BASE_URL` environment variable.
2. Never invent selectors: derive them from the case `locator`, acceptance criteria or fixture data; apply the hierarchy `data-test` > `getByRole`/`getByLabel` > `getByPlaceholder` > attribute fallback.
3. Never invent assertion text: copy expected values from `acceptance_criteria` or fixtures.
4. Never skip a step or assume page structure. If a step cannot be implemented from evidence, report an explicit gap instead of emitting a TODO, placeholder locator or empty assertion; the host rejects placeholders.

### API tests
1. Use the exact `path` and `method` from `api_contracts[]`; never infer endpoints.
2. Use fixture data or `request_schema` constraints; never guess field values.
3. Assert only fields in `response_schema` and codes in `status_codes[]`.
4. Cover each documented status code that an approved case targets.

### All tests
Preserve case IDs and requirement links (see `output-and-traceability.md`).

## Gaps

| Scenario | Action |
|---|---|
| Missing target URL | Read `BASE_URL`; if no route is evidenced, record a gap |
| Missing selector or expected text | Record a gap naming the step; do not guess |
| Missing fixture data | Record the missing fixture ID; do not invent data |
| Conflicting requirements | Record the ambiguity and implement only the explicit portion |
| Unsupported test type | Return `{ "error": true, "code": "UNSUPPORTED_TYPE", "message": "..." }`; supported types are UI, API, HYBRID |

Gap log shape: `{"total_tests": n, "generated": n, "with_gaps": n, "gaps": [{"test_id", "gap_type", "description", "action"}]}`.

## Gotchas
- Strict mode: `getByRole('radio', { name: 'Male' })` fails when "FeMale" exists; use `{ exact: true }` for short text.
- Unlinked labels: when `getByLabel` fails, fall back to `type`, `name`, `id` or `value` and verify the locator count.
- Shadow DOM: standard locators may fail inside shadow roots; use deep locators where needed.

## Scenarios
- Standard journey: linear flow with robust selectors and web-first assertions that passes first run.
- Form validation: invalid input and assertion that the specific error message becomes visible.
- Ambiguity: multiple similar elements are disambiguated with a parent locator or unique attribute, avoiding strict-mode violations.
