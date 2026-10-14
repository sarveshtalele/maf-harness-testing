---
name: playwright-test-generator
description: Adapt existing Playwright skill knowledge for optional read-only MCP observation of E2E journeys.
---

# Playwright observation only

This is the generation-only adaptation of MAF `skills/playwright-test-generator`. Despite the inherited skill name, it does **not** generate `.spec.ts` or execute cases. Use it only when the saved workflow enables Playwright observation and the user approves an allowed origin. Discover the installed MCP's actual inspect/read tools, show the selected tool and URL, and observe page structure, accessible roles, labels and navigation state.

Prefer stable `data-test`, role and label evidence when describing a UI step; do not invent a selector or expected text. Never submit a form, mutate business data, log in with credentials, run tests, install dependencies or infer a business rule from the current screen alone. Observation can clarify a step's wording; Jira/BRD/contract evidence remains the source of expected behavior. Record the observed URL, time and uncertainty as a note in the run memory.
