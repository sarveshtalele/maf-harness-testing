---
name: requirement-analysis
description: Build bounded source-linked requirements from deterministic repository facts, with stable host IDs and explicit gaps, without a requirements model call. Unverified feature text is context, not evidence of implemented behavior.
compatibility: Agentic QE Platform requirements artifact schema on Windows and macOS.
metadata:
  execution: host-static
---
# Requirement analysis

The host calls `maf_test_harness.repo.requirements.requirements_from_facts` and its existing schema/traceability adapter. This adapts the evidence-location and deterministic-intent principles of sarveshtalele/requirement-analysis-skill, reviewed at commit 3eae93ae5b5f8d25acc7b0240bf64ee6f75a6f01. That upstream package plans requested changes; it is not a source-proven business-requirement engine.

Use only facts whose actual source was supplied to the bounded analysis. Require existing contained source paths and original line references. Keep stable descriptive IDs through the host's existing adapter. Report candidates as candidates; do not turn heuristic matches into proven business behavior. Include source bodies as evidence for downstream planning.

Do not invent actors, acceptance criteria, HTTP codes, validation limits, security guarantees or compliance obligations. Empty supported evidence blocks requirements explicitly. File, byte, parser and requirement limits are gaps, not claims of completeness. Pasted repository knowledge stays unverified context. Review approval remains mandatory. Installation and runtime checks remain deferred until Test execution.

Output: the existing requirements artifact plus `analysis_mode: deterministic` and `model_calls: 0`. Planning, design and source generation can still use models; this skill does not claim a model-free entire lifecycle.

Execution proof: the host runs `scripts/analyze.py --request input/request.json --output output.json` through its reviewed host-tier script runner. The request is host-owned JSON; reverse engineering reads the approved clone, and requirements consumes its saved facts. The runner records script SHA-256, stage, exit code and duration. Successful JSON output is validated before the review gate. No model chooses or invokes these two scripts.
