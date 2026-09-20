# Repository Guide Reference

## Context7 documentation workflow

Resolve:

    Microsoft Agent Framework

Preferred Learn documentation library:

    /websites/learn_microsoft_en-us_agent-framework

Query one concept per call:
- Agent Skills and SkillsProvider
- function tools and approval_mode
- ChatMiddleware and FunctionMiddleware
- create_harness_agent and AgentSession
- WorkflowBuilder and human-in-the-loop
- checkpoints and resume

Use Context7 to confirm current APIs. Use the repository's pinned dependencies and working source to decide what is safe to change.

## Choose the correct primitive

| Requirement | Prefer |
|---|---|
| Domain instructions and reusable expertise | Agent Skill |
| Read a repository file or call a deterministic service | Function tool |
| Log every tool invocation | FunctionMiddleware |
| Enforce budget or security around model calls | ChatMiddleware |
| Compose skills, tools, middleware and stateful sessions | Agent Harness |
| Multi-stage routing, checkpoints, human review | Workflow |
| Dangerous action requiring confirmation | Tool approval and/or workflow human gate |

## Review checklist

- Is the skill narrow and portable?
- Is the SKILL.md frontmatter descriptive enough for discovery?
- Are large references kept outside the main skill body?
- Are tools bounded and host-validated?
- Are tool outputs size-limited?
- Are secrets excluded?
- Are write/destructive tools approval-gated?
- Is middleware policy-only rather than business logic?
- Is one AgentSession preserved for stateful harness turns?
- Is skill usage observable from tool traces?
- Are multi-stage transitions owned by a workflow instead of the model?
- Are checkpoints available before long-running or resumable execution?
