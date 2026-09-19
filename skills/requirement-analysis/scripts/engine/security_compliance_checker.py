"""
Security & Compliance Checker
Produces a deterministic checklist of security, compliance, and standards
items relevant to a requirement, based purely on its target_types and
domain_tags. Same intent always produces the same checklist — the rules
table below is the single source of truth (see
references/security-compliance-rules.md for the full mapping).
"""
from typing import Dict, List

from .requirement_parser import RequirementIntent

# category: "security" | "compliance" | "standard"
_TARGET_TYPE_RULES: Dict[str, List[Dict[str, str]]] = {
    "api_endpoint": [
        {"category": "security", "item": "Validate and sanitize all input parameters, query strings, and request bodies.", "reference": "OWASP ASVS V5 / A03:2021 Injection"},
        {"category": "security", "item": "Enforce authentication and authorization checks before processing the request.", "reference": "OWASP ASVS V4"},
        {"category": "security", "item": "Apply rate limiting / throttling to the new or changed endpoint.", "reference": "OWASP API Security Top 10 — API4"},
        {"category": "security", "item": "Return generic error messages; never leak stack traces or internal identifiers.", "reference": "OWASP ASVS V7"},
        {"category": "compliance", "item": "Update the OpenAPI/Swagger/GraphQL contract to reflect the change.", "reference": "API contract governance"},
        {"category": "standard", "item": "Follow existing route naming, versioning (e.g. /api/v1/...), and response envelope conventions."},
    ],
    "database": [
        {"category": "security", "item": "Use parameterized queries / ORM methods only — never build SQL via string concatenation.", "reference": "OWASP A03:2021 Injection"},
        {"category": "compliance", "item": "Add a forward-and-backward-compatible migration script; never edit a previously applied migration.", "reference": "Schema change governance"},
        {"category": "standard", "item": "Add or update an index if the new/changed column is used in lookups or joins."},
    ],
    "service": [
        {"category": "security", "item": "Validate inputs at the service boundary even if the caller already validated them (defense in depth).", "reference": "OWASP ASVS V5"},
        {"category": "standard", "item": "Add or extend unit tests covering new business-logic branches and edge cases."},
    ],
    "ui_component": [
        {"category": "security", "item": "Escape/encode any user-supplied data before rendering it (prevent XSS).", "reference": "OWASP A03:2021"},
        {"category": "standard", "item": "Match existing component structure, prop typing, and styling conventions."},
        {"category": "standard", "item": "Add accessibility attributes (labels, aria-*, keyboard navigation) for new interactive elements."},
    ],
    "config": [
        {"category": "security", "item": "Never hardcode secrets or credentials — use environment variables or a secrets manager.", "reference": "OWASP ASVS V14"},
        {"category": "compliance", "item": "Document new configuration values and their defaults in README / deployment manifest."},
    ],
    "test": [
        {"category": "standard", "item": "Ensure new tests cover both the happy path and at least one failure/edge case."},
    ],
}

_DOMAIN_TAG_RULES: Dict[str, List[Dict[str, str]]] = {
    "security_sensitive": [
        {"category": "security", "item": "Re-review the threat model for any change touching authentication, authorization, or session handling.", "reference": "OWASP ASVS V2/V3/V4"},
        {"category": "security", "item": "Never log raw passwords, tokens, or session identifiers.", "reference": "OWASP ASVS V7/V8"},
    ],
    "financial_critical": [
        {"category": "compliance", "item": "Never store raw payment card data — use a tokenized reference from the payment processor.", "reference": "PCI-DSS"},
        {"category": "compliance", "item": "Write an audit-log entry for every financial state change (amounts, status transitions)."},
    ],
    "pii_data": [
        {"category": "compliance", "item": "Minimize personal data collected; encrypt sensitive fields at rest and document the retention period.", "reference": "GDPR Art. 5 / Art. 25"},
    ],
    "external_integration": [
        {"category": "security", "item": "Verify signatures on inbound webhooks and use HTTPS for all outbound calls.", "reference": "OWASP ASVS V13"},
    ],
}

_ACTION_RULES: Dict[str, List[Dict[str, str]]] = {
    "remove": [
        {"category": "compliance", "item": "Confirm no other module, consumer app, or external client still depends on the removed code (run change-impact-analysis-skill first)."},
        {"category": "standard", "item": "Remove associated tests, docs, and dead config rather than leaving them orphaned."},
    ],
    "fix": [
        {"category": "standard", "item": "Add a regression test that reproduces the bug before the fix and passes after."},
    ],
}

# Always included regardless of intent.
_BASELINE_RULES: List[Dict[str, str]] = [
    {"category": "standard", "item": "Add or update unit tests for the change."},
    {"category": "standard", "item": "Run the project's formatter and linter before committing (see Formatting section)."},
    {"category": "standard", "item": "Update relevant documentation, README, or changelog entries."},
]


class SecurityComplianceChecker:
    def check(self, intent: RequirementIntent) -> List[Dict[str, str]]:
        items: List[Dict[str, str]] = []
        seen = set()

        def add_all(rules: List[Dict[str, str]]):
            for r in rules:
                key = (r["category"], r["item"])
                if key not in seen:
                    seen.add(key)
                    items.append(r)

        for ttype in intent.target_types:
            add_all(_TARGET_TYPE_RULES.get(ttype, []))

        for tag in intent.domain_tags:
            add_all(_DOMAIN_TAG_RULES.get(tag, []))

        add_all(_ACTION_RULES.get(intent.action, []))
        add_all(_BASELINE_RULES)

        # Deterministic order: security > compliance > standard, then by item text.
        order = {"security": 0, "compliance": 1, "standard": 2}
        items.sort(key=lambda r: (order.get(r["category"], 9), r["item"]))
        return items
