---
description: Configure a named Agentic QE workflow using installed agents, skills and MCP tools while retaining required approval gates.
argument-hint: "[workflow file or requested changes]"
---

Read `${CLAUDE_PLUGIN_ROOT}/references/workflow-guide.md` and `source-policy.md`. Copy the default workflow to a user-chosen project path, then edit prompts, installed agent names, and stage MCP selections according to `$ARGUMENTS`. Do not modify the plugin's default in place for one project. Resolve each requested MCP server/tool by inspecting tools available in this Claude Code session and matching a read-only or observation intent; show the selected server/tool and warn on unresolved names. Validate with the plugin's `scripts/qe.py validate-workflow FILE` using `python3` on macOS/Linux or `py -3` on Windows. Do not allow edits that remove stage gates, relax 95, bypass permission prompts or add test execution.
