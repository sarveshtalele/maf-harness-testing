"""
Requirement Parser
Converts a free-text feature/change request into a structured Intent:
action, target types, entities (symbol/file name candidates), and
domain tags (security / compliance hints). Pure dictionary + regex
matching — same input always produces the same Intent.
"""
import re
from dataclasses import dataclass, field
from typing import List


# Order matters: first matching action wins. "fix" checked before "modify"
# so "fix the broken login form" is not classified as a generic edit.
_ACTION_PATTERNS = [
    ("fix",    [r"\bfix(es|ed|ing)?\b", r"\bbug\b", r"\bbroken\b", r"\bissue\b", r"\bincorrect(ly)?\b", r"\bnot working\b"]),
    ("remove", [r"\bremove(s|d)?\b", r"\bdelete(s|d)?\b", r"\bdeprecate(s|d)?\b", r"\bdrop\b"]),
    ("add",    [r"\badd(s|ed|ing)?\b", r"\bcreate(s|d)?\b", r"\bnew\b", r"\bintroduce(s|d)?\b", r"\bimplement(s|ed)?\b", r"\bsupport for\b"]),
    ("modify", [r"\bmodify|update|change|edit|extend|enhance|refactor|rename\b"]),
]

_TARGET_TYPE_KEYWORDS = {
    "api_endpoint": ["endpoint", "api", "route", "controller", "rest api", "graphql", "request", "response", "http"],
    "database":     ["model", "entity", "table", "schema", "migration", "column", "field", "database", "db", "repository", "orm", "record"],
    "service":      ["service", "business logic", "usecase", "use case", "workflow", "process", "handler"],
    "ui_component": ["component", "page", "screen", "ui", "view", "form", "button", "frontend", "widget"],
    "config":       ["config", "setting", "environment variable", "env var", "feature flag", "deployment", "docker", "yaml", "manifest"],
    "test":         ["test", "unit test", "integration test", "e2e", "test case"],
}

_DOMAIN_KEYWORDS = {
    "security_sensitive":  ["auth", "authentication", "authorization", "login", "logout", "password", "token", "jwt", "oauth", "permission", "role", "session", "2fa", "mfa"],
    "financial_critical":  ["payment", "billing", "invoice", "transaction", "price", "discount", "refund", "subscription", "stripe", "paypal", "checkout"],
    "pii_data":            ["email", "phone", "address", "ssn", "date of birth", "dob", "profile", "personal data", "user data", "name field"],
    "external_integration": ["webhook", "notification", "email service", "sms", "third-party", "integration", "api key", "external api"],
}


@dataclass
class RequirementIntent:
    raw_text: str
    action: str
    target_types: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    domain_tags: List[str] = field(default_factory=list)


class RequirementParser:
    def parse(self, text: str) -> RequirementIntent:
        lower = text.lower()

        # Pick the action whose matching keyword occurs earliest in the
        # text (the verb the requirement actually opens with), not simply
        # the first category in _ACTION_PATTERNS that matches anywhere —
        # e.g. "Add a risk factor for deprecated dependencies" should be
        # "add", even though "deprecated" would also match the "remove"
        # pattern later in the sentence.
        action = "modify"
        best_pos = len(lower) + 1
        for act, patterns in _ACTION_PATTERNS:
            for p in patterns:
                m = re.search(p, lower)
                if m and m.start() < best_pos:
                    best_pos = m.start()
                    action = act

        target_types = [
            ttype for ttype, keywords in _TARGET_TYPE_KEYWORDS.items()
            if any(kw in lower for kw in keywords)
        ]

        domain_tags = [
            tag for tag, keywords in _DOMAIN_KEYWORDS.items()
            if any(kw in lower for kw in keywords)
        ]

        entities = self._extract_entities(text)

        return RequirementIntent(
            raw_text=text,
            action=action,
            target_types=target_types,
            entities=entities,
            domain_tags=domain_tags,
        )

    @staticmethod
    def _extract_entities(text: str) -> List[str]:
        """Pull candidate symbol/file names out of the requirement text."""
        entities: set = set()

        # Quoted identifiers: 'discount_code', "OrderService"
        for m in re.finditer(r"['\"]([A-Za-z0-9_\-./]+)['\"]", text):
            entities.add(m.group(1))

        # File-path-like tokens: src/api/users.py, app/models/order.rb
        for m in re.finditer(r"\b([\w\-]+/[\w\-./]+\.\w{1,5})\b", text):
            entities.add(m.group(1))

        # PascalCase identifiers: OrderService, UserController
        for m in re.finditer(r"\b([A-Z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*)\b", text):
            entities.add(m.group(1))

        # snake_case identifiers: discount_code, user_id
        for m in re.finditer(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", text):
            entities.add(m.group(1))

        # bare backtick / inline-code style tokens: `Order`, `cart.total`
        for m in re.finditer(r"`([^`]+)`", text):
            entities.add(m.group(1))

        return sorted(entities)
