# Clone progress and platform diagnostics

The reported Windows log shows real Git clone attempts followed by a second Ctrl+C interrupting `taskkill` during launcher shutdown. It does not include Git stderr, so it does not establish whether the network error came from GitHub, a proxy, certificate trust or credential handling.

## Corrected behavior

- Clone approval saves `active_stage=repo_analysis` before Git starts. The UI falls back to the pending gate for older runs and never renders a blank stage as “— is running”. Approved actions are labelled as executing rather than as new approval requests.
- Git stdout/stderr write directly to files while Git runs. `--progress` exposes real fetch/checkout output. Captured pipes previously hid this output until the process ended.
- Git uses its own process group. Timeout and **Cancel clone** terminate that run's process tree on Windows, macOS and Linux. File-backed output avoids waiting for EOF on pipes inherited by descendants after the parent is killed.
- Clone attempts share the existing 180-second network deadline; bounded process cleanup can add up to 25 seconds. Certificate verification remains enabled, no global Git settings change, and noninteractive credential-manager settings prevent unattended GUI/text prompts.
- Cancellation returns an error gate with Retry/Stop and retains partial source and output. A retry starts a fresh operation ID; an old cancellation cannot cancel the new operation.
- Launcher shutdown ignores repeated termination interrupts during cleanup and bounds Windows taskkill. Press Ctrl+C once and allow cleanup to finish. The outer Command Prompt may still ask “Terminate batch job”; that is cmd.exe behavior, not a workflow error.

## Actual files and directories

New run history is `<repo-root>/.maf-harness/runs` on Windows, macOS and Linux. For a checkout at `C:\Users\SarveshTalele\Documents\maf-harness-testing-new-ui`, runs are created in `C:\Users\SarveshTalele\Documents\maf-harness-testing-new-ui\.maf-harness\runs\<run-id>`. Service logs are in `<repo-root>/.maf-harness/logs`. These folders are git-ignored and created automatically. Older `~/.maf-harness/runs` and `.harness_runs` checkpoints remain discoverable in place; opening an old run shows its original path. `HARNESS_RUNS_DIR` may override the default. The run page displays the actual backend-resolved directory.

For each run:

| File | Purpose |
|---|---|
| `workflow.log` | API dispatch before an operation runs |
| `clone.log` | Retry attempt summaries |
| `clone_operation.json` | Current attempt, start time, status and output location |
| `clone-operations/<id>/stderr.log` | Live Git progress and actual error output |
| `clone-operations/<id>/stdout.log` | Git stdout |
| `run.json` | Saved workflow gate and state |
| `audit.jsonl` | Approval, tool-start and tool-completion evidence |

The UI displays live Git output and offers **Cancel clone** while Git is active. Logs → clone output remains available after cancellation, failure or completion. After Git completes, source inspection and the deterministic reverse-engineering skill run before the repository review gate; model calls remain zero for this phase.

If Windows cannot find a run directory, compare the UI's actual path with the checkout location (`cd` in Command Prompt) and Configuration → Debugging logs. Do not delete a run or create a substitute empty directory to repair saved state. If the process reports a proxy/TLS/permission failure, address that specific error using [Windows diagnostics](WINDOWS_SETUP_AND_DEBUGGING.md), then Retry stage.

## Starting and verifying

| OS | Start | Verify and exit |
|---|---|---|
| Windows | `start-windows.cmd` | `start-windows.cmd --smoke-test` |
| macOS | `./start-macos.command` | `./start-macos.command --smoke-test` |
| Linux | `bash start-linux.sh` | `bash start-linux.sh --smoke-test` |

All launchers use the same Python/Next launcher, Node 24 and Python 3.12. CI (`.github/workflows/ui.yml`) runs the clone-transport and Git-lifecycle tests, real GitHub clones (Windows Schannel and POSIX default TLS), actual launcher startup and shutdown, and browser journeys on the platform matrix. These checks verify the harness; they do not certify every target application or a particular corporate network.

## Strict generation update — October 6, 2026

[Current intake, batching, Java quality gates and scoped QA](PETSTORE_STRICT_TEST_GENERATION.md) supersede earlier truncation and final-retry acceptance behavior. New deterministic requirements retain all discovered declarations; installation/runtime checks remain at Test execution. Maven baseline unit/integration and generated results are separate, and runtime coverage is explicitly unmeasured. Run logs default to the checkout’s `.maf-harness` directory.
