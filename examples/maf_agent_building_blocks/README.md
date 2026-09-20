# MAF Agent Building Blocks

A small runnable reference for Microsoft Agent Framework:

    Agent Skill -> Function Tools -> Middleware Hooks -> Harness -> Session -> Run

This example is intentionally much smaller than the production harness in the repository. It uses the same public primitives so you can understand each layer before reading src/maf_test_harness.

## Files

~~~text
examples/maf_agent_building_blocks/
  main.py
  harness.py
  repo_tools.py
  middleware_hooks.py
  skills/
    repository-guide/
      SKILL.md
      references/
        checklist.md
~~~

## Run

From the repository root, install the existing pinned dependencies:

~~~bash
uv sync --extra dev
~~~

Load your model settings. The root project already documents OPENAI_API_KEY, OPENAI_BASE_URL and OPENAI_MODEL.

For a .env file:

~~~bash
set -a
source .env
set +a
~~~

Then run:

~~~bash
uv run python examples/maf_agent_building_blocks/main.py --repo .
~~~

Or supply a different prompt:

~~~bash
uv run python examples/maf_agent_building_blocks/main.py \
  --repo . \
  --prompt "Explain how this repository implements skills, tools, middleware and its harness."
~~~

## What happens

1. FileSkillsSource discovers the repository-guide skill.
2. SkillsProvider exposes the standard skill tools.
3. The harness primes the model by requiring load_skill for repository-guide.
4. Two bounded repository tools are available:
   list_repository_files and read_repository_file.
5. ChatAuditHook records model-call timing.
6. ToolAuditHook records every function invocation, including load_skill.
7. create_harness_agent composes the model, skill provider, tools, middleware and approval behavior.
8. one AgentSession is reused for the prime turn and the requested task.
9. the script prints the answer and an audit trace.

The example deliberately disables web search, file memory, todo, mode and compaction so the four requested building blocks remain visible.

## Context7

This sample was authored against the current Microsoft Learn documentation retrieved through Context7.

Resolved documentation library:

    /websites/learn_microsoft_en-us_agent-framework

Use Context7 for version-sensitive questions, but keep runtime behavior pinned and testable. See docs/AGENT_SKILLS_TOOLS_HOOKS_HARNESS.md for the exact workflow.
