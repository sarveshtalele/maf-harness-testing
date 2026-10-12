# MCP servers

Marketplace > **MCP servers** lets you add Model Context Protocol servers (Jira, Playwright, others) and give chosen
stages their tools. Everything is off until you enable it. Configuration links there.

## Model

`<user-data>/mcp_servers.json` holds, per server: `name` (slug), `transport` (`stdio` | `http`), `command`/`args`
(stdio), `url` (http; https only, http for localhost), `env_keys` (secret variable NAMES), `enabled`, `stages`
(default none), `allowed_tools` (auto-approved, read-only) and `approval_required_tools` (human approval), `preset`, and
`discovered_tools` (`[{name, description}]` from the last successful Test: at most 200 tools, descriptions redacted and
at most 300 characters). Only Test writes `discovered_tools`; a client-sent value is ignored.
**No secret values are ever in this file**; unknown fields (such as a pasted token) are rejected.

## Safety rules

- Only tools listed in `allowed_tools` or `approval_required_tools` are visible to the model (MAF `allowed_tools`);
  anything else, including tools a server adds later, does not exist for the agent. A server may list at most 200 tools
  (approved + approval-required), but at most 8 MCP tools reach a stage (stage tool budget is 20): a server whose
  exposed tools would exceed that is skipped in the stage, and the UI warns about it.
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

Atlassian: the endpoint is from the Atlassian MCP server README (v2 recommended; the v1 `/v1/sse` endpoint had an end date of 30 June 2026 and is not used). OAuth 2.1
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

## Import

`POST /mcp-servers/import` with `{"text": "..."}` accepts a stdio command line (`npx -y <pkg>`, `uvx <pkg>`, `docker run -i --rm <image>`;
a leading `KEY=value` is a secret) or an MCP JSON config (`mcpServers`, VS Code `servers`, a bare map or a single object).
stdio and streamable HTTP are supported; SSE is rejected. An `Authorization` header becomes the `BEARER_TOKEN` secret;
other headers are rejected. Servers are imported disabled with no stages and no tools. The response carries names and
secret NAMES only (`imported: [{source_key, name, secret_keys}]`, plus `servers`). The import is all or nothing, at most
20 servers and 100 kB of input.

## API

`GET /mcp-servers` (also `max_tools_per_server`, `max_tools_per_stage`), `POST /mcp-servers` (upsert), `POST /mcp-servers/import`,
`DELETE /mcp-servers/{name}`, `POST /mcp-servers/{name}/test` (connect, list tool names and count, store the catalogue),
`POST /mcp-servers/{name}/secrets` (body `{KEY: value}`; empty clears).
