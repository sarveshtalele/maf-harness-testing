---
name: api-contract-analyzer
description: Extract normalized API contracts from OpenAPI 3.x, Swagger 2.0, or JSON Schema inputs for downstream test design, coverage, and traceability. Use when an API specification is supplied; do not infer API contracts from prose alone.
---

# API Contract Analyzer

## Goal

Parse a supplied API specification into one deterministic, machine-readable contract model. This skill is optional in the overall platform: when no API specification is supplied, skip it and allow downstream skills to use requirement-analysis outputs only.

## Inputs

Accept OpenAPI 3.x YAML or JSON, Swagger 2.0 YAML or JSON, and JSON Schema in request or response payloads. **AsyncAPI, webhook, and event-driven contracts are explicitly out of scope.** Parse raw YAML/JSON directly when no local parser is available. Prefer resolving local `$ref` values inline when possible.

Write one contract report conforming to `assets/schema_templates.yaml`. The report contains:

- `api_contracts`: one entry for every path-and-method operation.
- `validation_rules`: explicit, source-derived rules useful to test design.
- `gaps_report`: non-fatal omissions and parsing limitations.

Downstream consumers are `test-design-generator`, `coverage-analyzer`, and `traceability-generator`.

## Steps

1. Extract every supported HTTP operation under every `paths` entry. Preserve its path, lowercase method, summary, descriptions, explicit status codes, tags, request body schema, response schemas, response headers, parameters, security, and deprecation status. Never silently drop an operation.
2. Use the supplied `operationId` when present. Otherwise create the stable fallback `<method>_<normalized-path>`, replacing path parameters and non-alphanumeric separators with single underscores and trimming underscores; for example, `POST /accounts/{accountId}/balance` becomes `post_accounts_accountid_balance`.
3. Merge path-level and operation-level parameters by `(name, in)`, with operation-level values taking precedence. Preserve each parameter's name, location, required flag, example/default when supplied, schema, and structured constraints.
4. Extract top-level `servers` (or Swagger `host`, `basePath`, and `schemes`) and server variables. Extract operation security when supplied, otherwise inherit top-level security. Include the referenced security-scheme type, location/name or flows, and scopes; never invent credentials or flows.
5. Extract operation `tags` and preserve them in the order declared.
6. For request and response content with multiple media types, retain every available type in `content_types` / `response_content_types`. Use the alphabetically first type as the primary `request_schema` or status-code schema in `response_schemas` for backward-compatible consumption.
7. Extract response headers (`responses[code].headers`) and include them in `response_headers` per status code, preserving `required`, `description`, and `schema` when present.
8. Extract `examples` from request body schemas and response body schemas, preserving the media-type key. Include empty arrays/objects when no examples are present.
9. Populate `field_constraints` as objects, never prose strings. For every relevant request field and parameter, preserve explicitly supplied `type`, `format`, `pattern`, `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`, `minLength`, `maxLength`, `minItems`, `maxItems`, `enum`, `nullable`, and `additionalProperties`. Include a field only when at least one of these properties is explicit or its type is available.
10. Emit a `traceability_links` array on every operation with one entry: `{source_type: "api_contract", source_id: <operationId>}`. This gives downstream `traceability-generator` a stable link without guessing.
11. When a request or response schema contains `discriminator`, extract it and include it under `discriminator` on the operation. Preserve `propertyName` and `mapping` verbatim. If `mapping` uses local `$ref` values, resolve them when possible; otherwise retain the raw mapping values.
12. Preserve only source information. Do not invent endpoints, fields, response bodies, rules, server URLs, authentication, defaults, or constraints.

## Edge Cases

- For a circular local `$ref`, stop at the first repeated reference, preserve the unresolved reference where possible, and add a note to `gaps_report.notes`.
- Preserve `allOf`, `anyOf`, and `oneOf` in the schema. Flatten `allOf` only when all component properties are resolvable without conflict; otherwise retain the composition intact.
- Preserve `discriminator` objects when present. Include `propertyName` and `mapping` under the operation's `discriminator` field so downstream test-design can generate type-specific contract tests.
- Preserve `additionalProperties`, including whether arbitrary fields are allowed and any associated schema.
- Preserve nullable fields using the source version's representation (`nullable` or a null type) and normalize it to `nullable` in `field_constraints` when explicit.
- Extract deprecated operations and set `deprecated: true`; add a deterministic note identifying the operation ID.
- A response without a body still belongs in `status_codes`; record its missing schema in `gaps_report.missing_schemas` rather than inventing one.

## Output contract and errors

- Return the structured error object below for an unparseable document, unsupported top-level version, or a document without a usable `paths` section. Do not return a partial contract in those cases.
- For a missing request or response schema on an otherwise usable operation, continue extraction and add a specific entry to `gaps_report.missing_schemas`.
- For an incomplete operation (for example, no responses), continue extraction and add its stable operation ID to `gaps_report.incomplete_endpoints`.

## Structured Error Object

```json
{
  "error": true,
  "code": "INVALID_INPUT",
  "message": "OpenAPI document is missing required 'paths' section.",
  "details": {"field": "paths", "issue": "missing"}
}
```

Use `MISSING_INPUT`, `INVALID_INPUT`, or `SCHEMA_VALIDATION_FAILED` with a specific message and only affected fields or files.

## Self-check

1. Validate the completed report against `assets/schema_templates.yaml`.
2. Confirm every extracted operation has `path`, `method`, a non-empty `operationId`, `status_codes`, and a corresponding `response_schemas` entry for each response that supplies a schema.
3. Confirm `gaps_report` is present, has every required key, and records all non-fatal missing schemas, incomplete operations, circular references, and deprecated endpoints.
4. Confirm every test-relevant supplied constraint and parameter is represented structurally in `field_constraints` or `parameters`.
5. Reconcile extracted operations with the source `paths` and confirm none was silently dropped.

## Example

See `examples/input.yaml` and `examples/output.json`. The example is schema-aligned and demonstrates a generated operation ID, structured constraints, and the required `gaps_report`.

## Success Criteria

- All source operations are deterministically represented or explicitly recorded as incomplete.
- Contract IDs are stable and usable by traceability consumers.
- Constraints, parameters, content types, server definitions, and security requirements remain machine-readable.
- The report is schema-valid, source-faithful, and contains an actionable gaps report.
