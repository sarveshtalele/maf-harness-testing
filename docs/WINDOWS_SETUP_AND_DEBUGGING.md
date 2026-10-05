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

Running the start script again reuses a healthy platform and prints its URL, without reinstalling dependencies or starting duplicate servers. Stop the original terminal with Ctrl+C before updating code or `.env`. If only a leftover service or another program occupies a default port, startup automatically chooses a free port and prints the actual URL. It does not kill the existing program. Explicit `--port` / `--api-port` values remain strict; choose a different value when occupied. A reused application stays owned by its original terminal, including when `--smoke-test` is used.

For an older downloaded version still rejecting port 8765, update to the latest ZIP. As an immediate workaround, after stopping the original terminal if it is available:

```cmd
start-windows.cmd --api-port 8766 --port 3001
```

Open the URL printed by the launcher. This does not erase any saved runs or stop unrelated processes.

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

## GitHub clone: Schannel `missing close_notify`

This is Git's TLS connection closing unexpectedly, after the application has started. A proxy, VPN, security software or an interrupted network connection may cause it; the error alone does not identify which one.

The harness retries interrupted transport operations within one 180-second deadline: configured TLS, then HTTP/1.1, then on Windows only an OpenSSL retry for the specific Schannel `missing close_notify` failure. Certificate verification stays enabled. No global Git settings change. Certificate-trust or repository-authentication failures are not retried. Partial clones and previous source are preserved in `clone-attempts`; `clone.log` records attempts in the run folder, and the final error is shown at the Retry gate.

Update Git for Windows from https://git-scm.com/download/win, reopen the terminal, then test connectivity without cloning or changing configuration:

```cmd
git --version
git -c http.version=HTTP/1.1 ls-remote https://github.com/sarveshtalele/requirement-analysis-skill.git HEAD
git -c http.version=HTTP/1.1 -c http.sslBackend=openssl -c http.sslVerify=true ls-remote https://github.com/sarveshtalele/requirement-analysis-skill.git HEAD
```

If the OpenSSL command reports an untrusted corporate certificate, obtain the approved CA configuration from your network administrator; OpenSSL may use a different trust store from Schannel. Do not disable certificate verification or revocation checking. After connectivity works, choose **Retry stage** in the application. A network outage cannot be repaired by changing the Node or Next version.

Windows can also briefly deny replacing `run.json` while a reader or antivirus holds it. State writes retry that sharing conflict for up to two seconds while retaining the previous valid file. Persistent permission failures remain visible errors; the harness does not bypass directory permissions.

## Verification scope

The CI matrix uses Node 24 on Windows and macOS, installs with npm command shims disabled to reproduce this class of failure, checks build/types and then runs the actual platform launcher smoke test and browser workflow. Only a successful Windows CI run confirms those checks ran on Windows; macOS tests do not certify an arbitrary Windows machine or corporate network.
