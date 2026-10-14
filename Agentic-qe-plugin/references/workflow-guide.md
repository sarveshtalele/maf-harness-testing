# Workflow configuration

Copy `workflows/default.json` to a project-controlled JSON file, edit its stage prompts, agent names, and `mcp_servers` / `tool_intents`, then run `qe.py validate-workflow FILE`. Pass the file to `qe.py init --workflow FILE` so a snapshot is saved in the run. A run never silently follows later edits to the template. The seven stage IDs and order, 95 floor, and two repair rounds are fixed to preserve gate semantics.

Install custom Claude Code plugins or skills through Claude Code's native `/plugin` interface or a configured marketplace. Put custom agents in a Claude Code plugin or project; use their loaded names in the workflow. Configure additional MCP servers in the host's Claude Code settings or plugin. A workflow only selects among already available tools and cannot install a server or bypass permissions. Keep read/search MCP access in intake and observation tools in the optional observation block. The default plugin ships Atlassian remote MCP and Playwright stdio MCP; Playwright observation starts disabled.

Every stage prompt is user-controlled but subordinate to the source, scope, review, and execution policy. A custom workflow may add a specialist perspective or output wording. It cannot omit approvals, allow test execution, accept low confidence, or make Jira writes. Display unresolved tool names in status and stop the affected stage.
