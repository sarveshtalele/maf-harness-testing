# Agentic QE Platform

Agentic QE Platform is an internal test-engineering tool for the team. Give it a GitHub repository (brownfield) or specs from documents or Jira Cloud (greenfield). It analyzes the code, plans and designs tests, generates unit and integration tests, and runs them after your approval. It ends with a QA report and a ZIP of the deliverables. It runs as a local web UI, as a command-line tool, or as an unsigned desktop app.

The repository is private and for the internal team only.

## Download and try it

There are three ways to run the platform. Pick one.

### (a) Desktop app

The desktop app is an Electron shell around the same backend and UI.

**Status:** no release has been published yet. Installers are built by GitHub Actions and appear under GitHub Releases once a release is cut:

- A tag `vX.Y.Z` runs `.github/workflows/desktop-release.yml`. It builds installers for Windows, macOS (arm64 and x64) and Linux, then creates a **draft** GitHub Release. The owner reviews and publishes it.
- A tag `vX.Y.Z-rcN` runs `.github/workflows/desktop-prerelease.yml`. It builds the installers and uploads them as workflow artifacts. It does not create a release.

Builds are **unsigned**, so the operating system warns on first launch.

- **macOS:** after copying the app to Applications, run `xattr -dr com.apple.quarantine "/Applications/Agentic QE.app"`. Or right-click the app and choose Open, once.
- **Windows:** SmartScreen shows "Windows protected your PC". Choose More info, then Run anyway.
- **Linux:** make the AppImage executable with `chmod +x Agentic-QE-*.AppImage`, then run it. It needs FUSE 2, or run it with `--appimage-extract-and-run`.

Set your model key in the app under **Configuration**, Model & API key. Where the operating system can encrypt it, the key is stored encrypted. Otherwise it is kept in memory for the session. The app shows only its last 4 characters.

More detail: [docs/DESKTOP.md](docs/DESKTOP.md) and [docs/RELEASING.md](docs/RELEASING.md).

**Build an installer yourself.** Build each OS on its own machine. You need Node.js 24 and uv.

```bash
uv sync --python 3.12 --extra dev --extra desktop
uv run python scripts/build_desktop.py --dry-run
uv run python scripts/build_desktop.py
```

The first command prints the steps. The second runs them. Installers are written to `desktop/release/`. You can add `--arch x64` or `--arch arm64` to build one architecture.

### (b) Source checkout with the one-click launchers

**Prerequisites**

- [Git](https://git-scm.com/).
- Python 3.12. uv can install it for you.
- [uv](https://docs.astral.sh/uv/getting-started/installation/).
- [Node.js 24](https://nodejs.org/). Exactly version 24 is required. The launchers and npm refuse other versions. With nvm: `nvm install 24` and `nvm use 24`.
- For the repositories you test: their toolchains, such as a JDK with Maven or Gradle for Java, or the .NET SDK. See [docs/ENVIRONMENTS.md](docs/ENVIRONMENTS.md).

**Steps**

1. Clone the repository and switch to the default integration branch:

```bash
git clone https://github.com/sarveshtalele/maf-harness-testing.git
cd maf-harness-testing
git checkout develop
```

2. Create your settings file. The launchers also create it if it is missing.

```bash
cp .env.example .env
chmod 600 .env
```

On Windows PowerShell, use `Copy-Item .env.example .env`.

3. Open `.env` and set your gateway values:

```dotenv
OPENAI_API_KEY=<your-key>
OPENAI_BASE_URL=<your-openai-compatible-gateway-url>
OPENAI_MODEL=<a-model-that-supports-tool-calling>
```

You can skip this step and set the key later in the UI under **Configuration**. Never commit `.env`.

4. Start the platform with the launcher for your system:

```bash
./start-macos.command
```

```bash
bash start-linux.sh
```

```powershell
.\start-windows.cmd
```

The first start installs the Python and frontend dependencies and builds the UI. This takes a few minutes. The launcher then starts two local services:

- The web UI at **http://127.0.0.1:3000**. Open it in your browser.
- The Python API at **127.0.0.1:8765**. It accepts only local connections and requires a private token that the launcher generates.

If port 3000 or 8765 is busy, the launcher picks the next free port and prints it. Keep the terminal open. Press Ctrl+C to stop both services. The launcher checks Node 24 before it installs anything.

### (c) Headless command line

The CLI runs the same workflow without a browser. Run it from the repository root after step 1 and step 3 above.

```bash
uv sync --extra dev
uv run maf-harness run https://github.com/owner/repo
uv run maf-harness run https://github.com/owner/repo --no-execution
uv run maf-harness run https://github.com/owner/repo --auto-approve
uv run maf-harness trace .maf-harness/runs/<run-id>
uv run maf-harness verify-run .maf-harness/runs/<run-id>
```

- Without `--auto-approve`, each gate asks you in the terminal.
- `--auto-approve` is your explicit consent to clone the repository, install its dependencies and run the generated tests on this machine.
- `--no-execution` stops after test generation. Nothing is installed and no test runs.
- Ctrl+C stops the run.

Run folders are written to `.maf-harness/runs/<run-id>`.

## First run walkthrough

These steps use the UI. The CLI asks the same questions in the terminal.

1. Open **Configuration**. Check that a key is configured. Only its last 4 characters are shown.
2. Open **New run**.
3. Choose a mode:
   - **Brownfield** for an existing repository. Enter its public GitHub URL.
   - **Greenfield** for new work. Give it documents (`.md`, `.txt` or `.docx`; PDF needs the `pypdf` package) or a Jira Cloud project. A specs skill turns them into requirements. The Jira token stays in memory only.
4. Choose the test types: **Unit**, **Integration**, or both.
5. Start the run. The run page opens on the **Review** tab. The workflow pauses at each gate and waits for you. For each review gate you can approve, revise, skip or stop. Tool gates let you approve or deny an action.

The gates, in order:

1. Approve the repository clone (a machine action).
2. Review the repository analysis.
3. Review the deterministic requirements.
4. Review the test plan.
5. Review the test design, in batches by component.
6. Review the generated test source.
7. Approve installing the test environment (a machine action).
8. Approve running the tests (a machine action).
9. Review the scoped QA report. The deliverable ZIP is built after this.

Nothing is installed and no test runs before gates 7 and 8. Static source checks run during generation. Build and collection checks run only after the environment is installed.

After the run:

- **QA report** tab: results per case, and the status of each requirement: new case, pre-existing witness, excluded, or unexplained gap.
- **Generated tests** tab: the test files that were produced.
- **Deliverables** folder in the run folder (`.maf-harness/runs/<run-id>/deliverables/`): the ZIP.
- **Errors** tab: every error in the run, grouped. Each error has a code and a suggested fix. The meanings are in [docs/ERROR_PLAYBOOK.md](docs/ERROR_PLAYBOOK.md).
- **Logs**, **Audit**, **Observability** and **Test reports** tabs: the evidence trail.

To stop a run early, use **Stop** in the UI and confirm. The run stops after the current step ends (within about 30 seconds). Rerunning a stopped run starts again from the active stage. Earlier evidence is kept under `attempts/`.

## Verify your install

Run these from the repository root.

Check the prerequisites and the ports without starting anything:

```bash
uv run python scripts/start_ui.py --check-only
```

Start both services, check that the home page and the authenticated backend connection work, then stop. This does not need a model key:

```bash
./start-macos.command --smoke-test
```

On Linux use `bash start-linux.sh --smoke-test`. On Windows use `.\start-windows.cmd --smoke-test`.

Check the bundled skills against their lock file:

```bash
uv run maf-harness lint-skills --strict
```

## Developer environment

Full details are in [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md), section 1.

```bash
uv sync --python 3.12 --extra dev --extra e2e
cd frontend
npm ci --include=dev
cd ..
cd desktop
npm ci
cd ..
uv run python scripts/start_ui.py
```

The last command runs the API and the UI in dev mode with hot reload.

Common checks:

```bash
uv run pytest -q --ignore=tests/targets
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run maf-harness lint-skills --strict
uv run python scripts/gen_architecture.py --check
cd frontend
npm run typecheck
npm run build
cd ..
cd desktop
npm test
cd ..
```

Browser tests are in [section 1.6](docs/DEVELOPER_GUIDE.md#16-run-each-test-layer).

**Branches.** `develop` is the default branch and the integration branch. Work on `feature/*` branches and open pull requests into `develop`. `main` holds released code. `release/x.y` is cut from `develop` for QA. `new-ui` mirrors `develop`. See [CONTRIBUTING.md](CONTRIBUTING.md).

**CI workflows** (in `.github/workflows/`):

| Workflow | When it runs | What it checks |
|---|---|---|
| `ci.yml` | Pushes to `main`, `develop`, `release/**`, and pull requests | Ruff lint and format, architecture map, skill lock, and the Python tests on Ubuntu (Python 3.11 and 3.12), Windows and macOS. Job `secrets-scan` fails on credential-shaped strings. |
| `ui.yml` | Same branches, plus manual runs | Python UI tests, real GitHub clones, frontend typecheck and build, launcher smoke checks on all three systems, and the Playwright browser tests. |
| `nightly.yml` | Every day at 02:00 UTC, and manual runs | Full Python test suite on Linux. |
| `desktop-prerelease.yml` | Tags `v*-rc*`, and manual runs | Builds desktop installers as workflow artifacts. |
| `desktop-release.yml` | Tags `v*.*.*` | Builds desktop installers and creates a draft GitHub Release. |

No workflow runs the desktop `npm test`. Run it yourself when you change `desktop/`.

## Configuration and security

- Settings come from `.env`, the UI **Configuration** page, or the shell. The order is: runtime value in the UI, then the environment, then `.env`. See [.env.example](.env.example) for all options.
- Never commit `.env`, run evidence (`.maf-harness/`), or `docs/agent-framework.pdf`. The `.gitignore` covers the first two.
- The UI shows only the last 4 characters of any key.
- The API listens on loopback only and needs a token.
- **Local execution is not a sandbox.** Generated tests and the commands of the repositories you test run as your user on this machine. Use a disposable machine or account for repositories you do not trust.

## Troubleshooting

- [docs/WINDOWS_SETUP_AND_DEBUGGING.md](docs/WINDOWS_SETUP_AND_DEBUGGING.md): Windows setup, long paths, antivirus locks and log locations.
- [docs/ERROR_PLAYBOOK.md](docs/ERROR_PLAYBOOK.md): every error code, whose fault it is, and what to do.
- [docs/CLONE_AND_PLATFORM_DIAGNOSTICS.md](docs/CLONE_AND_PLATFORM_DIAGNOSTICS.md): clone failures and platform checks.
- [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md), section 5: where evidence lives and how to debug a run.

Launcher logs are in `.maf-harness/logs/launch-*.log`. Run evidence is in `.maf-harness/runs/<run-id>/`.

## More documentation

- [docs/README.md](docs/README.md): index of all documentation.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): design and guarantees.
- [CLAUDE.md](CLAUDE.md): project rules for contributors and coding agents.
- [CONTRIBUTING.md](CONTRIBUTING.md): branches, commits and pull requests.
