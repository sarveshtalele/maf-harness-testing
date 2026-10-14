---
name: api-contract-analyzer
description: Extract explicit API contract details only from a supplied OpenAPI, Swagger or JSON Schema source.
---

# Optional API contract analysis

Adapted from MAF `skills/api-contract-analyzer`. Use only when the user supplies a contract. For OpenAPI 3.x or Swagger 2.0, inventory every path and method, preserving supplied operation ID, parameters, request/response media types, explicit status codes, security scheme, server values, field constraints and deprecation. For JSON Schema alone, extract schema constraints only; never invent an endpoint.

Resolve local `$ref` when possible; record unresolved or circular references, missing responses and unsupported composition as gaps. Keep path-level and operation-level parameter precedence and preserve explicit `allOf`, `anyOf`, `oneOf`, discriminator and `additionalProperties` semantics. Link each useful operation or constraint to its exact source and approved story/control. Contract facts may refine E2E UI-to-API or external-system expectations; they do not authorize isolated unit/integration cases.

Check that every source operation is accounted for or named in a gap. Do not invent URLs, authentication behavior, defaults, response bodies or status codes.
