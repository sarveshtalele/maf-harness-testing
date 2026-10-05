# Windows setup and debugging

## Node.js and the missing Next command

Use **Node.js 24 LTS**. `Next.js 16.3.8` is the framework version, not Node 16. The launcher prints the actual Node version and executable path and rejects other Node majors before installation.

In a new Command Prompt:

```cmd
where node
node --version
where npm
npm --version
npm config get bin-links
npm config get omit
```

If Node is missing or an older installation appears first on PATH, install/update Node 24 from https://nodejs.org/en/download and reopen the terminal. Check `node --version` again; it must start with `v24.`. Installing Node does not change an already-open terminal's PATH. Do not mistake the framework version for the runtime version.

The reported `'next' is not recognized` message means npm could not resolve the Next CLI command after installation. The transcript alone does not prove whether a missing shim, npm configuration, antivirus quarantine or a partial install caused it. The revised scripts avoid depending on `node_modules/.bin/next.cmd`: they invoke the installed Next JavaScript entry point with the verified Node executable. Setup explicitly includes development dependencies and restores npm binary links. The package scripts use direct Node entry points too.

## Update and start

For a Git checkout:

```cmd
git switch new-ui
git pull --ff-only origin new-ui
start-windows.cmd
```

For a downloaded ZIP, download the latest `new-ui` ZIP and extract it to a new folder. Preserve your existing `.env` privately and copy it into the new project root. Do not publish that file. Run `start-windows.cmd` in the new folder after checking Node 24. The launcher does not overwrite an existing `.env`.

Both services use the same launcher. Open http://127.0.0.1:3000 and keep the terminal open. Ctrl+C stops both services. Use the command below to verify both services and stop them automatically:

```cmd
start-windows.cmd --smoke-test
```

The launcher checks occupied ports before reinstalling or building, pins Python 3.12 with uv, installs locked dependencies and builds Next. Missing prerequisites fail visibly. UTF-8 is enabled for Python services and executed Python scripts, including paths and skill instructions containing non-ASCII text.

## Where to find every kind of log

The launcher prints the exact paths, including overrides. Default locations are:

| Evidence | Location |
|---|---|
| Frontend installation/build, API and Next startup/service output | `%USERPROFILE%\.maf-harness\logs\launch-*.log` |
| API dispatch and failures, before a model or command runs | `%USERPROFILE%\.maf-harness\runs\<run-id>\workflow.log` |
| Approvals, stage transitions, repairs and host errors | Run folder `audit.jsonl` |
| Model/tool invocations, durations and token usage | Run folder `trace.jsonl` |
| Installation command stdout/stderr | Run folder `environment\attempt-*\...\stdout.log` and `stderr.log` |
| Test command stdout/stderr and reports | Run folder `executions\attempt-*\...\stdout.log` and `stderr.log` |
| Saved state and current gate | Run folder `run.json` |

**Configuration → Debugging logs** shows the actual directories. Open a run's **Logs**, **Audit**, **Observability** and **Test reports** for its evidence. Service logs remain available when startup failed before a run exists. Configured credentials are masked in saved launcher output and UI presentation; do not share raw `.env` or unreviewed target-generated logs.

Open logs in Explorer:

```cmd
explorer "%USERPROFILE%\.maf-harness\logs"
explorer "%USERPROFILE%\.maf-harness\runs"
```

Follow the latest service log in PowerShell:

```powershell
$qeLog = Get-ChildItem "$env:USERPROFILE\.maf-harness\logs\launch-*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content $qeLog.FullName -Tail 80 -Wait
```

## A run appears to hang after submitting a GitHub URL

1. Check the launcher terminal and saved service log for an API or frontend failure. Verify the UI is opened at localhost:3000, through the launcher; running Next alone does not connect the authenticated backend.
2. Configure the real gateway URL, model and key in `.env`, then restart. Placeholder credentials do not establish a working model connection.
3. On **Review**, approve the repository-clone gate. Entering a URL intentionally does not bypass this gate. Approve each later review separately. This is distinct from the Working state while an operation runs.
4. During Working, inspect **Logs** and **Observability**. Dispatch is logged immediately; model operations are bounded by `HARNESS_MODEL_TIMEOUT_SECONDS` (default 300 seconds per operation) and expose a retry/error gate instead of waiting indefinitely. Static repository/requirements scripts have no model calls.
5. A Git clone has its own timeout and error gate. Corporate proxies, Git installation and gateway reachability must be checked on the execution machine. Do not disable certificate validation to work around network problems.
6. Runtime installation occurs only at Test execution setup. Missing Java/Maven/WildFly or other prerequisites are separate from a frontend startup defect. Review the repository-specific setup guide and command output.
7. Use **Rerun from step** after the active operation reaches a gate. Prior evidence is archived under `attempts/<id>`; downstream approvals are reset and normal tool approvals apply. Reconnect restores saved state; it does not automatically replay an interrupted command.

## Verification scope

The CI matrix uses Node 24 on Windows and macOS, installs with npm command shims disabled to reproduce this class of failure, checks build/types and then runs the actual platform launcher smoke test and browser workflow. Only a successful Windows CI run confirms those checks ran on Windows; macOS tests do not certify an arbitrary Windows machine or corporate network.
