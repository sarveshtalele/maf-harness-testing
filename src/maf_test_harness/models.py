from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TestCaseReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1)
    confidence: int = Field(ge=0, le=100)
    verdict: Literal["supported", "needs_revision", "insufficient_evidence"]
    reason: str = Field(min_length=1, max_length=1500)
    source_files: list[str] = Field(
        min_length=1,
        max_length=8,
        description="Existing repository implementation paths supporting the judgment; never newly generated test paths.",
    )


class TestReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviews: list[TestCaseReview] = Field(min_length=1, max_length=8)


class DesignCaseReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1)
    verdict: Literal["approved", "revise", "reject"]
    confidence: int = Field(ge=0, le=100)
    reason: str = Field(min_length=1, max_length=1500)
    suggested_target: str | None = Field(default=None, max_length=300)


class DesignReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviews: list[DesignCaseReview] = Field(min_length=1, max_length=60)


class RepoAnalysis(BaseModel):
    repo_url: str
    repo_name: str
    commit_sha: str | None = None
    file_count: int = 0
    source_file_count: int = 0
    source_files: list[str] = Field(default_factory=list)
    languages: dict[str, int] = Field(default_factory=dict)
    frameworks: list[str] = Field(default_factory=list)
    package_files: list[str] = Field(default_factory=list)
    test_files: list[str] = Field(default_factory=list)
    test_frameworks: list[str] = Field(default_factory=list)
    likely_test_directories: list[str] = Field(default_factory=list)
    has_tests: bool = False
    notes: list[str] = Field(default_factory=list)


RequirementCategory = Literal[
    "functional",
    "validation",
    "security",
    "error_handling",
    "data",
    "integration",
    "ui",
    "cli",
    "performance",
    "accessibility",
]
TestLevel = Literal[
    "unit", "integration", "api", "ui", "e2e", "cli", "security", "performance", "accessibility"
]


class ActorDraft(BaseModel):
    """A user persona / calling party observed in the repository (e.g. anonymous visitor, admin, CLI user)."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    source_files: list[str] = Field(min_length=1, max_length=6)


class RequirementEvidenceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=700)
    evidence: str = Field(min_length=1, max_length=900)
    source_files: list[str] = Field(min_length=1, max_length=8)
    category: RequirementCategory
    actor: str = Field(min_length=1, max_length=80, description="Actor name from `actors`, or 'system'.")
    source_symbol: str = ""
    source_line: int = Field(default=0, ge=0)


class AcceptanceCriterionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_ref: int = Field(ge=1, description="1-based index into `requirements`.")
    text: str = Field(min_length=1, max_length=500)
    source_files: list[str] = Field(min_length=1, max_length=4)


class ValidationRuleDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=400)
    source_files: list[str] = Field(min_length=1, max_length=4)


class ApiContractDraft(BaseModel):
    """Only explicit, documented HTTP contracts (route + method + status codes visible in source/spec)."""

    model_config = ConfigDict(extra="forbid")
    method: str = Field(min_length=3, max_length=7)
    path: str = Field(min_length=1, max_length=200)
    status_codes: list[str] = Field(min_length=1, max_length=8)
    source_files: list[str] = Field(min_length=1, max_length=4)


class EnvironmentNeedDraft(BaseModel):
    """Something a person must have to build/run/test the repository (read from README, manifests, CI files)."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["runtime", "dependency", "test_tool", "service", "configuration"]
    how_to_install: str = Field(
        min_length=1, max_length=400, description="Exact command or step, isolated env preferred."
    )
    evidence_file: str = Field(
        max_length=200, description="File that states it; empty only when inferred from the stack."
    )


class RequirementsDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_name: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=900)
    actors: list[ActorDraft] = Field(min_length=1, max_length=8)
    requirements: list[RequirementEvidenceDraft] = Field(min_length=1, max_length=30)
    acceptance_criteria: list[AcceptanceCriterionDraft] = Field(max_length=40)
    validation_rules: list[ValidationRuleDraft] = Field(max_length=20)
    api_contracts: list[ApiContractDraft] = Field(max_length=25)
    environment_needs: list[EnvironmentNeedDraft] = Field(max_length=12)
    exclusions: list[str] = Field(max_length=12)
    assumptions: list[str] = Field(max_length=12)
    gaps: list[str] = Field(max_length=12)


class RequirementsInventoryDraft(RequirementsDraft):
    """Merged host inventory; provider limits apply to batches, not the repository."""

    requirements: list[RequirementEvidenceDraft] = Field(min_length=1)
    intake: dict = Field(default_factory=dict)


class TestArea(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=800)
    level: Literal[
        "unit",
        "integration",
        "api",
        "ui",
        "contract",
        "e2e",
        "regression",
        "performance",
        "security",
        "functional",
        "mixed",
    ]
    risk: Literal["high", "medium", "low"]
    evidence_files: list[str] = Field(max_length=12)
    scenarios: list[str] = Field(min_length=1, max_length=12)


class TestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """Compact, provider-enforceable JSON contract for the planning agent.

    All fields are required so OpenAI-compatible structured-output providers can
    enforce the schema. Bounded arrays prevent model repetition from consuming
    the whole completion and producing truncated JSON.
    """

    objective: str = Field(min_length=1, max_length=1200)
    recommended_frameworks: list[str] = Field(min_length=1, max_length=8)
    setup_actions: list[str] = Field(max_length=20)
    areas: list[TestArea] = Field(min_length=1, max_length=40)
    execution_order: list[str] = Field(min_length=1, max_length=20)
    assumptions: list[str] = Field(max_length=15)
    gaps: list[str] = Field(max_length=15)


class TraceabilityLinkDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["requirement", "acceptance_criterion", "validation", "api_contract"]
    id: str = Field(min_length=1, max_length=160)


class TestDesignCaseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """Authoring-only test case. The host assigns id/type and derived metrics."""

    title: str = Field(min_length=1, max_length=220)
    priority: Literal["P0", "P1", "P2"]
    level: TestLevel = Field(description="Automation level; decides the framework the generator uses.")
    actor: str = Field(min_length=1, max_length=80, description="Actor name from requirements, or 'system'.")
    target: str = Field(
        min_length=1, max_length=200, description="Function/class, endpoint, page or command under test."
    )
    description: str = Field(min_length=1, max_length=1000)
    preconditions: list[str] = Field(max_length=6)
    data_notes: str = Field(max_length=400, description="Concrete inputs/fixtures; empty when none.")
    expected_result: str = Field(min_length=1, max_length=1000)
    steps: list[str] = Field(min_length=1, max_length=10)
    traceability_links: list[TraceabilityLinkDraft] = Field(min_length=1, max_length=8)


class TestDesignDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """Compact JSON contract used only for model generation.

    The host converts this into the marketplace test-design-generator canonical
    document, including metadata, stable IDs, summary, traceability matrix,
    coverage analysis and gaps report.
    """

    positive_tests: list[TestDesignCaseDraft] = Field(max_length=12)
    negative_tests: list[TestDesignCaseDraft] = Field(max_length=12)
    boundary_tests: list[TestDesignCaseDraft] = Field(max_length=10)
    validation_tests: list[TestDesignCaseDraft] = Field(max_length=10)
    contract_tests: list[TestDesignCaseDraft] = Field(max_length=8)
    gaps: list[str] = Field(max_length=15)


class GeneratedTestFileDraft(BaseModel):
    """One generated test file.

    Source is returned as ``lines`` (one array item per source line, no trailing newline, indentation kept)
    instead of one big string: live runs showed some gateways/models drop the newline escapes inside a long
    JSON string ("import sysimport pytest..."), which no validator can repair. Line arrays carry the line
    structure explicitly; the host joins them into ``content``.
    """

    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=240)
    lines: list[str] = Field(
        min_length=1, max_length=400, description="Source code, one array item per line."
    )
    case_ids: list[str] = Field(min_length=1, max_length=1)


class GeneratedFilesDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    """Bounded JSON contract for executable, statically traceable test generation."""

    selected_language: str = Field(min_length=1, max_length=80)
    selected_framework: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=600)
    files: list[GeneratedTestFileDraft] = Field(min_length=1, max_length=8)
