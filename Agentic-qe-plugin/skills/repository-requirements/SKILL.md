---
name: repository-requirements
description: Identify observable technical behavior from an approved brownfield diff without inventing business requirements.
---

# Repository requirements for E2E design

Adapted from MAF `skills/repository-requirements`. Read only the bounded diff evidence from `reverse-engineering` and approved related files. Prefer implementation bodies and public configuration over examples or comments. Distinguish an observed implementation behavior from an approved Jira or BRD requirement.

For each candidate, record the actor or external system boundary, exact changed file and line evidence, observable result, and whether a source story/control also supports it. A code-only candidate belongs in an open question or implementation observation until the user approves it as a requirement. Include error paths, permissions, persistence and integration behavior only when the changed evidence reaches them. Do not infer a status code, selector, limit, security guarantee or remote-service behavior from a name or signature.

Return empty optional lists where the evidence is silent. Record documentation/code conflicts and low testability as gaps. Do not expand from the diff to a full repository inventory.
