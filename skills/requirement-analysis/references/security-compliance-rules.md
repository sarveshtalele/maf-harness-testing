# Security & Compliance Rules Reference

Full table of every rule produced by
`engine/security_compliance_checker.py`. Each requirement's checklist is
the **union** of:

1. `_TARGET_TYPE_RULES[ttype]` for each `ttype` in `intent.target_types`
2. `_DOMAIN_TAG_RULES[tag]` for each `tag` in `intent.domain_tags`
3. `_ACTION_RULES[intent.action]` (only `remove` and `fix` defined)
4. `_BASELINE_RULES` (always included)

Deduplicated by `(category, item)`, then sorted by category
(`security` → `compliance` → `standard`) and item text.

---

## Target-Type Rules

### `api_endpoint`

| Category | Item | Reference |
|----------|------|-----------|
| security | Validate and sanitize all input parameters, query strings, and request bodies. | OWASP ASVS V5 / A03:2021 Injection |
| security | Enforce authentication and authorization checks before processing the request. | OWASP ASVS V4 |
| security | Apply rate limiting / throttling to the new or changed endpoint. | OWASP API Security Top 10 — API4 |
| security | Return generic error messages; never leak stack traces or internal identifiers. | OWASP ASVS V7 |
| compliance | Update the OpenAPI/Swagger/GraphQL contract to reflect the change. | API contract governance |
| standard | Follow existing route naming, versioning (e.g. `/api/v1/...`), and response envelope conventions. | — |

### `database`

| Category | Item | Reference |
|----------|------|-----------|
| security | Use parameterized queries / ORM methods only — never build SQL via string concatenation. | OWASP A03:2021 Injection |
| compliance | Add a forward-and-backward-compatible migration script; never edit a previously applied migration. | Schema change governance |
| standard | Add or update an index if the new/changed column is used in lookups or joins. | — |

### `service`

| Category | Item | Reference |
|----------|------|-----------|
| security | Validate inputs at the service boundary even if the caller already validated them (defense in depth). | OWASP ASVS V5 |
| standard | Add or extend unit tests covering new business-logic branches and edge cases. | — |

### `ui_component`

| Category | Item | Reference |
|----------|------|-----------|
| security | Escape/encode any user-supplied data before rendering it (prevent XSS). | OWASP A03:2021 |
| standard | Match existing component structure, prop typing, and styling conventions. | — |
| standard | Add accessibility attributes (labels, aria-*, keyboard navigation) for new interactive elements. | — |

### `config`

| Category | Item | Reference |
|----------|------|-----------|
| security | Never hardcode secrets or credentials — use environment variables or a secrets manager. | OWASP ASVS V14 |
| compliance | Document new configuration values and their defaults in README / deployment manifest. | — |

### `test`

| Category | Item | Reference |
|----------|------|-----------|
| standard | Ensure new tests cover both the happy path and at least one failure/edge case. | — |

---

## Domain-Tag Rules

### `security_sensitive`

| Category | Item | Reference |
|----------|------|-----------|
| security | Re-review the threat model for any change touching authentication, authorization, or session handling. | OWASP ASVS V2/V3/V4 |
| security | Never log raw passwords, tokens, or session identifiers. | OWASP ASVS V7/V8 |

### `financial_critical`

| Category | Item | Reference |
|----------|------|-----------|
| compliance | Never store raw payment card data — use a tokenized reference from the payment processor. | PCI-DSS |
| compliance | Write an audit-log entry for every financial state change (amounts, status transitions). | — |

### `pii_data`

| Category | Item | Reference |
|----------|------|-----------|
| compliance | Minimize personal data collected; encrypt sensitive fields at rest and document the retention period. | GDPR Art. 5 / Art. 25 |

### `external_integration`

| Category | Item | Reference |
|----------|------|-----------|
| security | Verify signatures on inbound webhooks and use HTTPS for all outbound calls. | OWASP ASVS V13 |

---

## Action Rules

### `remove`

| Category | Item | Reference |
|----------|------|-----------|
| compliance | Confirm no other module, consumer app, or external client still depends on the removed code (run change-impact-analysis-skill first). | — |
| standard | Remove associated tests, docs, and dead config rather than leaving them orphaned. | — |

### `fix`

| Category | Item | Reference |
|----------|------|-----------|
| standard | Add a regression test that reproduces the bug before the fix and passes after. | — |

---

## Baseline Rules (always included)

| Category | Item |
|----------|------|
| standard | Add or update unit tests for the change. |
| standard | Run the project's formatter and linter before committing (see Formatting section). |
| standard | Update relevant documentation, README, or changelog entries. |

---

## Extending This Table

To add a new rule:

1. Add the rule dict (`{"category": ..., "item": ..., "reference": ...}` —
   `reference` is optional) to the appropriate dict in
   `scripts/engine/security_compliance_checker.py`.
2. Mirror the addition in this file so the two stay in sync.
3. `category` must be `"security"`, `"compliance"`, or `"standard"` —
   anything else sorts last (`order.get(category, 9)`) but is still shown.
