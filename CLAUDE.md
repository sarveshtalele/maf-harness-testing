# Agentic QE Platform — contributor guidance

Current branch: `new-ui`. Next.js frontend plus a loopback Python HTTP adapter over the checkpointed Microsoft Agent Framework Harness. The CLI uses the same workflow. Windows, macOS and Linux launchers use Node.js 24 and Python 3.12; Next.js has its own framework version.

## Commands

```bash
uv sync --extra dev
./start-macos.command                 # macOS; Windows: start-windows.cmd; Linux: bash start-linux.sh
uv run maf-harness run https://github.com/owner/repo
uv run maf-harness trace .maf-harness/runs/<run-id>
uv run maf-harness verify-run .maf-harness/runs/<run-id>
uv run maf-harness lint-skills
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest -q --ignore=tests/targets
cd frontend
npm ci --include=dev
npm run typecheck
npm run build
npm test
```

Gateway settings belong in the git-ignored `.env`. Never print or commit credentials, local run evidence or `agent-framework.pdf`. Do not delete files or rewrite user evidence without explicit authorization.

## Workflow and guardrails

Clone tool approval → repository analysis review → deterministic requirements review → planning review → component-batched design review → generated source review → Test execution setup/install approval → execution approval → scoped QA review and ZIP.

New runs use workflow version 2. `environment_setup` follows generation, and no target workspace, installation or runtime collection is created in earlier phases. Existing runs retain their saved stage order. An incomplete installation remains a blocking error; never relax repository dependency pins or replace missing application dependencies with fake tests. Runtime collection/build checks run only after execution approval. Static source checks can run during generation.

The two deterministic host skills execute reviewed scripts and record hashes, duration and output. Model stages use the assigned skills through the recorded Microsoft Agent Framework provider. Unverified repository knowledge cannot grant permissions or become executable commands. Model artifacts and machine actions retain their existing approval gates; explicit CLI auto-approval is an authorized alternative.

Retain the complete discovered declaration inventory, batch model work, and reject unexplained coverage omissions even on the final retry. Tests must exercise actual source behavior, use valid positive fixtures, preserve native framework conventions and complement existing tests. Scope generated, baseline unit and baseline integration evidence separately. Static traceability must never be labelled measured line/branch/mutation or complete application coverage.

## Ownership

- `workflow.py`: checkpointed Harness, MAF stages, gates and transitions.
- `workflow_steps/{planning,design,generation,execution}.py`: stage operations and resumable batches.
- `repo/{analyzer,git_transport,reverse_engineering,requirements,stack,routes}.py`: clone and static intake.
- `qa/{quality,report,triage,packaging}.py`: source witnesses, scoped results, remediation and exports.
- `envs/{environment,regression,runners,execution,platform_compat,toolchains}.py`: approved setup/execution and platform behavior.
- `ui/`: authenticated loopback adapter, presentation and evidence APIs; `frontend/`: Next.js interface.
- `skills/`: locked skill packages and reviewed script hashes. Refresh only the hashes for files intentionally edited; never bypass lock verification.

Run history defaults to `<repo-root>/.maf-harness/runs`, service logs to `<repo-root>/.maf-harness/logs`; explicit overrides and old history discovery are retained. Shared toolchain/usage caches have their separate configured locations. Local execution isolation is not a security sandbox.

See [strict generation and QA scope](docs/PETSTORE_STRICT_TEST_GENERATION.md), [architecture](docs/ARCHITECTURE.md), [setup](README.md), [Windows debugging](docs/WINDOWS_SETUP_AND_DEBUGGING.md) and [modular workflow](docs/MODULAR_WORKFLOW_IMPLEMENTATION.md). Dated plans and audit reports describe historical evidence; their old counts are not current release claims.
