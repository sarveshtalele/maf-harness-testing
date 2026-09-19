# OpenAPI Extraction Reference

## Version Mapping

| Version | JSON Schema Draft | Key Differences                                                                 |
|---------|-------------------|--------------------------------------------------------------------------------|
| OpenAPI 3.0.x | Draft 07 | `nullable: true` for nullability; `exclusiveMinimum`/`exclusiveMaximum` are numbers |
| OpenAPI 3.1.x | Draft 2020-12 | `type` arrays include `"null"` (e.g., `["string", "null"]`) instead of `nullable`; `exclusiveMinimum`/`exclusiveMaximum` are booleans paired with `minimum`/`maximum` |
| Swagger 2.0 | Draft 04 (custom) | Body/form params; `definitions`, `securityDefinitions`, and response `schema` used |

### Version-Specific Extraction Rules

**For OpenAPI 3.0:**
- Read `paths`, `servers`, `components.securitySchemes`, request `content`, and response `content`.
- Normalize `nullable: true` to `nullable: true` in `field_constraints`.

**For OpenAPI 3.1:**
- Read the same top-level keys as 3.0.
- When `type` is an array containing `"null"`, normalize it to `type: <primary_type>` plus `nullable: true` in `field_constraints`.
- When `exclusiveMinimum`/`exclusiveMaximum` are booleans, preserve the paired `minimum`/`maximum` values as the constraint boundaries.

**For Swagger 2.0:**
- Construct servers from `schemes`, `host`, and `basePath`.
- Read body/form parameters and `definitions`, `securityDefinitions`, and response `schema`.

## Parameters and Security

Combine path-level and operation-level parameters by `(name, in)` with the operation-level definition taking precedence. Preserve `path`, `query`, `header`, and `cookie` locations. Extract security requirements as declared arrays and resolve their named scheme only when it is defined locally; record an unresolved scheme in `gaps_report.notes`.

## Content and Schemas

Sort media-type keys alphabetically. Retain all types, then use the first as the primary request or response schema. Preserve schemas verbatim when composition cannot be safely flattened. For circular `$ref`, retain the reference and note the cycle instead of guessing an expansion.

### Discriminator (Polymorphism)

When `discriminator` is present on a schema, extract it and include it on the operation:

```json
{
  "propertyName": "type",
  "mapping": {
    "payment": "#/components/schemas/Payment",
    "refund": "#/components/schemas/Refund"
  }
}
```

Do not invent mappings. If the schema does not declare a discriminator, leave the field `null`.

## Constraints

Capture constraints as JSON properties, not descriptions. Use dotted field paths for nested object properties (`customer.address.postcode`) and `[]` for array-item fields (`items[].quantity`). Required-field paths correspond to the object declaring `required`.

### Constraint Normalisation by OpenAPI Version

| Source Property | OpenAPI 3.0 Value | OpenAPI 3.1 Value | Output `field_constraints` |
|---|---|---|---|
| `nullable` | `true` | N/A (use `type` array) | `nullable: true` |
| `type: ["string", "null"]` | N/A | present | `type: string`, `nullable: true` |
| `exclusiveMinimum` | `5` | `true` (with `minimum: 5`) | `exclusiveMinimum: 5` |
| `exclusiveMaximum` | `100` | `true` (with `maximum: 100`) | `exclusiveMaximum: 100` |

## Gap Entries

Use stable, actionable entries:

```json
{
  "operationId": "post_payments",
  "context": "response",
  "code": "201",
  "message": "Response 201 has no schema."
}
```

For circular references:

```json
{
  "operationId": "get_accounts_accountid_balance",
  "context": "response",
  "code": null,
  "message": "Circular ref #/components/schemas/Account detected."
}
```

Never mark an invalid document as a non-fatal gap: return the structured error object instead.
