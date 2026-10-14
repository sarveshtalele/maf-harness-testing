---
name: requirement-analysis
description: Derive approved, source-cited functional and technical E2E requirements with stable links.
---

# Functional and technical requirement analysis

Adapted from MAF `skills/requirement-analysis`. Consume approved `stories.json`, `sources.json` and the approved brownfield scope when present. Assign stable `FR-*` IDs to externally observable functions and `TR-*` IDs to explicit technical constraints, including performance, security, accessibility or interoperability when the source states them. Each item has a direct quote, valid story IDs and control IDs, and an E2E observation point.

Preserve every supported claim across batches; a model output limit must not silently truncate the inventory. Compare source sections for contradictions and record them. Use implementation facts only within the approved diff. A code-only observation is a question until the user accepts it; it is not automatically a new BA rule. Do not invent actors, endpoints, status codes, thresholds, compliance promises or acceptance criteria.

Return an empty technical list when there is no technical evidence. Surface parser limits, unsupported evidence and untestable claims as explicit gaps. Check each FR/TR against the exact source quote and control before saving `requirements.json`.
