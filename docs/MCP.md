# MCP servers

Configuration > **MCP servers** lets you add Model Context Protocol servers (Jira, Playwright, others) and give chosen
stages their tools. Everything is off until you enable it.

## Model

`<user-data>/mcp_servers.json` holds, per server: `name` (slug), `transport` (`stdio` | `http`), `command`/`args`
(stdio), `url` (http; https only, http for localhost), `env_keys` (secret variable NAMES), `enabled`, `stages`
(default none), `allowed_tools` (auto-approved, read-only) and `approval_required_tools` (human approval), `preset`.
**No secret values are ever in this file**; unknown fields (such as a pasted token) are rejected.

## Safety rules

- Only tools listed in `allowed_tools` or `approval_required_tools` are visible to the model (MAF `allowed_tools`);
  anything else, including tools a server adds later, does not exist for the agent. At most 8 tools per server and 8
  MCP tools per stage (stage tool budget is 20).
- `allowed_tools` run without asking; `approval_required_tools` use MAF `always_require` approval. After connecting, the
  runtime forces `always_require` on any exposed tool that is not auto-approved.
- Approval requests are answered by `MAFHarnessRuntime.mcp_approver(stage, [{tool, arguments}]) -> [bool]`. **With no
  approver (the default) approval-required tools are not shown to the model at all** (trace note `mcp_approval_hidden`),
  so only `allowed_tools` are usable; a stray approval request is answered with a refusal and the model carries on. The
  workflow's existing human "tool" gate is for host commands between stages, not for mid-turn model tool calls, so wiring
  an interactive approval UI is a follow-up.
- Existing hooks apply to MCP calls (function middleware: pre/post-tool hooks, secret redaction), and each call is
  traced with category `mcp`. Output from MCP tools is described to the model as untrusted evidence.
- A server that cannot connect (timeout 25 s), lacks a secret, or would exceed the stage tool budget is skipped; the
  stage continues, and `state["mcp_unavailable"]` plus a trace event `mcp_unavailable` record why (redacted).
- Secrets: values come from an in-process store (set through `POST /mcp-servers/{name}/secrets`, never written to disk
  by Python) or from `HARNESS_MCP_SECRET_<SERVER>_<KEY>` environment variables. In the desktop app the keychain
  (`safeStorage`) stores them and the main process passes them as those variables and pushes them to the running
  backend. They are added to the redaction hook and never logged. stdio env values go only to the spawned process; an
  http token is sent as `Authorization` (`Bearer <token>`, or verbatim when it already contains a space, such as
  `Basic ...`) and only to the server's own origin.
- Env names that change code loading or networking cannot be configured: prefixes `DYLD_`, `LD_`, `NODE_`, `PYTHON`,
  `NPM_CONFIG_`, anything ending in `_PROXY`, and `PATH`, `BASH_ENV`, `ENV`, `PERL5LIB`, `RUBYOPT`, `JAVA_TOOL_OPTIONS`,
  `SSL_CERT_FILE`, `HOME`, `COMSPEC`.
- Deleting a server, or changing its url/transport/command, discards its secrets (env-supplied values are ignored until
  new ones are set). Env names encode `-` in the server name as `__` (`HARNESS_MCP_SECRET_ATLASSIAN__JIRA_BEARER_TOKEN`).
- Mutating API calls return 409 while a run operation is active.

## Presets

| Preset | Transport | Auto-approved | Needs approval |
| --- | --- | --- | --- |
| `atlassian-jira-cloud` | http `https://mcp.atlassian.com/v2/mcp` | `searchJiraIssuesUsingJql`, `getJiraIssue`, `getVisibleJiraProjects` | `createJiraIssue`, `editJiraIssue`, `addCommentToJiraIssue` |
| `playwright` | stdio `npx @playwright/mcp@latest` | `browser_snapshot`, `browser_console_messages`, `browser_network_requests` | `browser_navigate`, `browser_click`, `browser_type`, `browser_take_screenshot` |

Atlassian: the endpoint is from the Atlassian MCP server README (v2 recommended; `/v1/sse` ends 30 June 2026). OAuth 2.1
is interactive and **not supported**; an org admin must enable API-token auth, then paste a service-account API key
(Bearer) or `Basic <base64(email:token)>` as the token. v2 may expose discovery tools rather than the v1 names above,
so run **Test** and edit the tool lists (or add `?tools=all` to the URL). Tool names are unverified defaults.

Playwright: navigation and clicks have side effects on live sites, hence approval. Needs Node.js; on Windows `npx` is
resolved to `npx.cmd` with `shutil.which`.

## Greenfield intake

`spec_sources.jira_mcp = {server, jql | issue_keys, tool?, cloud_id?}` fetches stories through a configured server instead of
REST. The tool must be on the server's auto-approved list (this call bypasses the model approval gate) and must return
Jira search JSON (`{"issues": [...]}`); sections map exactly as in the REST path. `cloud_id` (`[A-Za-z0-9-]{1,64}`) is passed as the tool's `cloudId` argument, which Atlassian tools require; no other
arguments can be supplied. Not yet exposed in the UI form.

## API

`GET /mcp-servers`, `POST /mcp-servers` (upsert), `DELETE /mcp-servers/{name}`, `POST /mcp-servers/{name}/test`
(connect, list tool names and count), `POST /mcp-servers/{name}/secrets` (body `{KEY: value}`; empty clears).
