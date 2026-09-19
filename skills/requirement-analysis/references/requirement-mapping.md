# Requirement Mapping Reference

This document is the single source of truth for how
`engine/requirement_parser.py` and `engine/location_resolver.py` map
free-text requirements to structured intent and scores. Update this file
whenever the dictionaries or scoring constants in those modules change.

---

## 1. Action Detection

`_ACTION_PATTERNS` (checked in this order, but **earliest match position
in the text wins** — see [DOCUMENTATION.md §3.1](../DOCUMENTATION.md#31-action-classification)):

| Action | Trigger patterns (regex, case-insensitive) |
|--------|----------------------------------------------|
| `fix` | `fix(es\|ed\|ing)?`, `bug`, `broken`, `issue`, `incorrect(ly)?`, `not working` |
| `remove` | `remove(s\|d)?`, `delete(s\|d)?`, `deprecate(s\|d)?`, `drop` |
| `add` | `add(s\|ed\|ing)?`, `create(s\|d)?`, `new`, `introduce(s\|d)?`, `implement(s\|ed)?`, `support for` |
| `modify` (default) | `modify`, `update`, `change`, `edit`, `extend`, `enhance`, `refactor`, `rename` |

**Example:** *"Add a risk factor for deprecated dependencies"* → `add`
("Add" at position 0 beats "deprecated" matching the `remove` pattern
later in the sentence).

---

## 2. Target Type Detection

`_TARGET_TYPE_KEYWORDS` — any number of types can match (substring,
case-insensitive, on the whole requirement text):

| target_type | Keywords |
|-------------|----------|
| `api_endpoint` | endpoint, api, route, controller, rest api, graphql, request, response, http |
| `database` | model, entity, table, schema, migration, column, field, database, db, repository, orm, record |
| `service` | service, business logic, usecase, use case, workflow, process, handler |
| `ui_component` | component, page, screen, ui, view, form, button, frontend, widget |
| `config` | config, setting, environment variable, env var, feature flag, deployment, docker, yaml, manifest |
| `test` | test, unit test, integration test, e2e, test case |

---

## 3. Domain Tag Detection

`_DOMAIN_KEYWORDS` — drives extra security/compliance rules and a path
domain-token score bonus:

| domain_tag | Keywords |
|------------|----------|
| `security_sensitive` | auth, authentication, authorization, login, logout, password, token, jwt, oauth, permission, role, session, 2fa, mfa |
| `financial_critical` | payment, billing, invoice, transaction, price, discount, refund, subscription, stripe, paypal, checkout |
| `pii_data` | email, phone, address, ssn, date of birth, dob, profile, personal data, user data, name field |
| `external_integration` | webhook, notification, email service, sms, third-party, integration, api key, external api |

---

## 4. Entity Extraction

Five regex passes, results unioned and sorted:

| Form | Regex | Example |
|------|-------|---------|
| Quoted string | `['"]([A-Za-z0-9_\-./]+)['"]` | `'discount_code'` |
| File-path-like | `\b([\w\-]+/[\w\-./]+\.\w{1,5})\b` | `src/api/users.py` |
| PascalCase | `\b([A-Z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*)\b` | `OrderService` |
| snake_case | `\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b` | `discount_code` |
| Backtick-wrapped | `` `([^`]+)` `` | `` `Order` `` |

### Entity variants (`_entity_variants`)

Each entity is expanded to a set of lowercase variants so different
naming conventions match each other:

```
"DiscountCode" / "discount_code" / "discount-code" / "discountCode"
  → {"discountcode", "discount_code", "discount-code", "discountcode", "discountcode", "discountcode"}
```

(Splitting on `[_\-\s]+|(?=[A-Z])` then re-joining as snake/kebab/joined/
camel/Pascal — duplicates collapse via the set.)

---

## 5. Location Scoring Formula

For every file in the codebase index, for every extracted entity's
variant set:

| Signal | Points | Condition |
|--------|--------|-----------|
| Path token exact match | **+10** | a variant is exactly one of the file's path tokens |
| Path token partial match | **+5** | a variant (len ≥ 3) is a substring of, or contains, a path token (only if no exact match for that entity) |
| Symbol exact match | **+15** | a variant equals a symbol's lowercased name (deduped per symbol) |
| Symbol partial match | **+8** | a variant (len ≥ 3) is a substring of a symbol's lowercased name (only if not already exact-matched) |
| Module type match | **+6** (once per file) | `file.module_type ∈ intent.target_types` |
| Domain token match | **+4** per tag | a domain tag's keyword (joined, no spaces) is one of the file's path tokens |
| Content match | **+3 per distinct entity, capped at +9** | only computed if `score > 0`; file content (lowercased) contains any variant (len ≥ 3) of that entity |

**Ranking:** sort by `(-score, path)`. Top `--top-n` (default 5) kept.

**Confidence threshold:** `MIN_CONFIDENT_SCORE = 6`. If the top
candidate's score is below this (or there are no candidates), a
`new_file_suggestion` is generated instead (see
[DOCUMENTATION.md §5.4](../DOCUMENTATION.md#54-new-file-suggestion---_suggest_new_file)).

---

## 6. Suggestion Text — `_ADD_HINT`

When `action == "add"` and a candidate file has no matched symbols, the
"what to do" text is derived from the file's `module_type`:

| module_type | Hint |
|-------------|------|
| `api_endpoint` | add a new route/endpoint handler |
| `service` | add a new function/method |
| `database` | add a new model field, class, or migration |
| `ui_component` | add a new component/element |
| `config` | add the new configuration entry |
| `test` | add a new test case |
| `library` | add a new function/helper |
| `module` | add the new logic |
| *(other / unmapped)* | update this file |

---

## 7. Module Type Classification Precedence

(Identical to `change-impact-analysis-skill`'s `impact_analyzer.py` —
kept in sync so both skills agree on a file's role.)

1. `test` — path matches test patterns
2. `database` — path matches model/migration/schema/repository patterns
3. `config` — path matches config/env/docker/k8s/helm patterns
4. `ui_component` — path matches `.jsx/.tsx/.vue/.svelte` or
   `components?/pages?/views?/`
5. `api_endpoint` — content matches framework route-decorator patterns
6. Path-keyword fallback: `controller|handler|endpoint|route|resource` →
   `api_endpoint`; `service|usecase|interactor|business` → `service`;
   `util|helper|common|shared|lib|core` → `library`
7. Any recognized language → `module`
8. Otherwise → `other`
