# Microsoft Agent Framework: Skills, Tools, Hooks, and Harness

This guide is a compact map of the four building blocks used by this repository.

Official Microsoft Agent Framework documentation:
- Agent Skills: https://learn.microsoft.com/en-us/agent-framework/agents/skills
- Function tools: https://learn.microsoft.com/en-us/agent-framework/agents/tools/function-tools
- Middleware shared state: https://learn.microsoft.com/en-us/agent-framework/concepts/agents/middleware/shared-state
- Agent/run middleware scope: https://learn.microsoft.com/en-us/agent-framework/concepts/agents/middleware/agent-vs-run-scope
- Agent Harness: https://learn.microsoft.com/en-us/agent-framework/agents/harness
- Harness getting started: https://learn.microsoft.com/en-us/agent-framework/get-started/harness
- Checkpoints: https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints

## 1. Mental model

Use the layers for different jobs:

| Layer | Purpose | MAF primitive | In this repository |
|---|---|---|---|
| Skill | Portable domain instructions, resources, optional reviewed scripts | SkillsProvider + SKILL.md | skills/*/SKILL.md and src/maf_test_harness/maf_runtime.py |
| Tool | Deterministic capability the model can call | @tool / FunctionTool | src/maf_test_harness/agent_tools.py |
| Hook | Cross-cutting behavior around chat or function execution | ChatMiddleware / FunctionMiddleware | BudgetGuard and ToolTraceMiddleware in maf_runtime.py |
| Harness | Stateful agent shell that composes model, skills, tools, middleware, approvals, sessions, planning and other providers | create_harness_agent | MAFHarnessRuntime in maf_runtime.py |
| Workflow | Multi-stage orchestration and human review gates | WorkflowBuilder, Executor, WorkflowContext | src/maf_test_harness/workflow.py |

A useful rule is: put knowledge in a skill, deterministic operations in tools, cross-cutting policy in middleware, and lifecycle/orchestration in the harness or workflow.

## 2. Use Context7 before writing version-specific MAF code

For this guide, Context7 resolved Microsoft Agent Framework to:

    /websites/learn_microsoft_en-us_agent-framework

A good Context7 workflow is:

1. Resolve the library name "Microsoft Agent Framework".
2. Select /websites/learn_microsoft_en-us_agent-framework when you want the Learn documentation.
3. Query one concept at a time.
4. Compare the returned example with the version pinned by the project.
5. Validate the API against existing working code in this repository before changing production code.

Suggested Context7 queries:

~~~text
Python Agent Skills documentation: SkillsProvider setup, SKILL.md format,
progressive disclosure, resources, scripts, and harness integration.
~~~

~~~text
Python function tools documentation: @tool, schemas, approval_mode,
registration on create_harness_agent, and safe tool design.
~~~

~~~text
Python middleware documentation: ChatMiddleware, FunctionMiddleware,
agent-level and run-level middleware, shared state, and logging.
~~~

~~~text
Python Agent Harness documentation: create_harness_agent, sessions,
tool approval middleware, skills_paths or skills_provider, and interactive runs.
~~~

Context7 is an authoring-time documentation source here. The runtime example in examples/maf_agent_building_blocks intentionally does not depend on Context7 or network access.

## 3. Agent Skills

An Agent Skill is a portable package of instructions and optional resources/scripts. MAF advertises skill metadata first, loads the full skill only when needed, then reads resources or runs reviewed scripts on demand. This is progressive disclosure and keeps the context small.

Minimal structure:

~~~text
skills/
  repository-guide/
    SKILL.md
    references/
      checklist.md
~~~

Minimal SKILL.md:

~~~markdown
---
name: repository-guide
description: Review a repository and explain its agent architecture.
compatibility: Python host using Microsoft Agent Framework Agent Skills.
---

# Repository Guide

## Goal
Explain the repository using verified evidence.

## Rules
1. Read repository files through approved tools.
2. Do not invent behavior not supported by source.
3. Separate skills, tools, middleware, harness, and workflow concerns.
~~~

In the production harness, the important implementation is src/maf_test_harness/maf_runtime.py:
- FileSkillsSource discovers file-based skills.
- FilteringSkillsSource exposes only the skills assigned to a stage.
- DeduplicatingSkillsSource avoids duplicate advertisements.
- SkillsProvider adds load_skill, read_skill_resource, and run_skill_script.
- read-only skill operations are auto-approved only for trusted skill sources.
- the host verifies from FunctionMiddleware traces that the assigned skill was actually loaded.

That last verification is important: declaring a skill and proving that the model used it are different things.

## 4. Function tools

Use tools for bounded, deterministic operations that should not be guessed by the model.

Example:

~~~python
from typing import Annotated
from agent_framework import tool

@tool(name="read_repository_file", approval_mode="never_require")
def read_repository_file(
    path: Annotated[str, "Repository-relative file path"],
) -> str:
    ...
~~~

Design guidance:
- keep the input schema small and explicit;
- return bounded text or structured JSON;
- validate paths and arguments in the host;
- do not put credentials in tool results;
- prefer read-only tools for analysis;
- use approval for destructive or externally visible actions;
- record calls in middleware for observability.

The production implementation is src/maf_test_harness/agent_tools.py. It exposes exact repository facts such as symbol locations, module APIs, routes, import resolution, static validation and build checks.

## 5. Hooks in MAF are middleware

Agent Framework does not need a separate "hook" abstraction for most lifecycle interception. Use middleware.

Use ChatMiddleware when the policy surrounds a model/chat call.

Use FunctionMiddleware when the policy surrounds a function-tool call.

Example:

~~~python
from agent_framework import FunctionMiddleware

class ToolAuditHook(FunctionMiddleware):
    async def process(self, context, call_next):
        name = context.function.name
        try:
            await call_next()
        finally:
            print(name, context.result)
~~~

Good middleware uses:
- logging and tracing;
- token or budget enforcement;
- security checks;
- timing and metrics;
- result decoration;
- shared run state.

The repository already has two strong examples in maf_runtime.py:
- BudgetGuard extends ChatMiddleware and reserves/settles token budget around model calls.
- ToolTraceMiddleware extends FunctionMiddleware and records every MAF tool invocation.

Keep business logic out of middleware. Middleware should be reusable policy around execution.

## 6. Agent Harness

The Agent Harness is the stateful shell around an agent. Current Microsoft documentation exposes the Python factory create_harness_agent.

A harness can compose:
- model client;
- instructions;
- skills_provider or skills_paths;
- tools;
- middleware;
- sessions;
- tool approval;
- todo/mode providers;
- memory or file access;
- web search;
- compaction;
- loops and background agents when enabled.

Minimal shape:

~~~python
from agent_framework import SkillsProvider, create_harness_agent

agent = create_harness_agent(
    client=client,
    agent_instructions="Review the repository using evidence.",
    skills_paths=skills_root,
    tools=tools,
    middleware=middleware,
    auto_approval_rules=[
        SkillsProvider.read_only_tools_auto_approval_rule,
    ],
    disable_web_search=True,
)

session = agent.create_session()
result = await agent.run("Review this repository.", session=session)
~~~

Always pass a session when the harness approval middleware is active. The session is also what carries harness state across turns.

The example under examples/maf_agent_building_blocks shows a small complete implementation using the same APIs as this repository.

## 7. Harness versus Workflow

Do not make the harness responsible for every stage transition.

Use the harness for an agent's execution environment.

Use WorkflowBuilder when you have multiple deterministic stages, branching, checkpoints, or human review gates.

This repository uses both:
- MAFHarnessRuntime creates a harness agent for each model stage.
- workflow.py owns repository analysis, environment setup, stage order, human approvals, generation, execution and QA.

This separation is one of the strongest parts of the current design.

## 8. Human approval

There are two approval layers to keep separate.

Tool approval protects individual tool calls. Skills tools require approval by default; trusted read-only skill tools can be auto-approved.

Workflow human-in-the-loop protects stage transitions. WorkflowContext.request_info plus response_handler is appropriate when a human must review an artifact before the workflow continues.

For production systems:
- auto-approve only deterministic read-only operations you trust;
- require approval for writes, shell execution, deployment, external messages, purchases, destructive changes, or broad filesystem access;
- record the decision and the exact artifact/tool arguments that were approved.

## 9. Mapping this repository

Start with these files in this order:

1. skills/test-planning/SKILL.md
   A small example of a real file-based Agent Skill.

2. src/maf_test_harness/agent_tools.py
   Shows bounded @tool definitions and stage-specific tool exposure.

3. src/maf_test_harness/maf_runtime.py
   Shows SkillsProvider composition, create_harness_agent, ChatMiddleware, FunctionMiddleware, sessions, tool approval and skill-use verification.

4. src/maf_test_harness/workflow.py
   Shows WorkflowBuilder, Executor, WorkflowContext, handlers, checkpoints and human review gates.

5. docs/ARCHITECTURE.md
   Shows the complete production design.

## 10. Recommended implementation order for a new project

1. Create one SKILL.md with a narrow goal and deterministic output contract.
2. Add only the minimum read-only tools needed to obtain facts.
3. Add FunctionMiddleware tracing before adding more tools.
4. Add ChatMiddleware for budget, security or telemetry policy.
5. Create a harness agent and preserve a session across turns.
6. Add explicit tool approval rules.
7. Add a WorkflowBuilder only when you truly have multiple stages or human gates.
8. Add checkpoints before unattended or long-running execution.
9. Test the host-side validators independently from the model.
10. Keep model prompts small by using progressive disclosure and bounded tool results.

## 11. Common mistakes

Avoid:
- putting repository I/O directly inside a SKILL.md;
- giving every stage every tool;
- exposing a shell when a bounded function tool is enough;
- auto-approving write tools;
- mixing stage orchestration into middleware;
- relying on a model statement such as "I used the skill" instead of tracing the load_skill call;
- resending every skill body on every turn;
- treating a generated test as valid before host-side syntax/build/collection checks pass.

The production harness already addresses most of these failure modes. The new example is intentionally smaller so each primitive is easy to see.
