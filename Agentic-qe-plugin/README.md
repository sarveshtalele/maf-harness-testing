# Agentic QE for Claude Code

Agentic QE turns Jira stories, BRDs and other approved specifications into **reviewed, source-traced end-to-end test cases**. It also supports an existing repository: it reads a selected Git diff, maps changed files to the relevant approved stories, and generates cases **only for those mapped stories**. It produces a test plan, case catalog, traceability matrix, Markdown report, progress dashboard and decision log. It does not execute the target application's tests or generate unit/integration tests.

The plugin follows the durable artifacts, ordered stages and human gates used by Anthropic's [code-modernization plugin](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-modernization). It is a separate plugin on the `feature/agentic-qe-plugin` branch; earlier MAF application releases remain untouched. Its plugin version is independent of the MAF platform's v1.0.7 release tag.

## Install from GitHub

Requirements: [Claude Code](https://code.claude.com/docs/en/plugins/install), Python 3.11+, Git for brownfield runs, access to this GitHub repository, and `pypdf` for text-based PDFs. Use `python3` on macOS/Linux or `py -3` on Windows. Node.js and `npx` are needed only when you enable Playwright MCP observation.

The plugin lives in a subdirectory, so this branch includes a Claude Code marketplace manifest at the repository root. While it remains on this branch, install it with:

```sh
claude plugin marketplace add sarveshtalele/maf-harness-testing#feature/agentic-qe-plugin
claude plugin install agentic-qe@maf-agentic-qe --scope user
claude plugin list
```

In Claude Code, type `/agentic-qe:qe-run` to confirm the command is available. The [Claude Code install guide](https://code.claude.com/docs/en/plugins/install) describes user, project and local scopes. If the repository is private, Git credentials on your machine must be able to clone it. A local development session can load a checkout without installation:

```sh
claude plugin validate --strict /absolute/path/to/Agentic-qe-plugin
claude --plugin-dir /absolute/path/to/Agentic-qe-plugin
```

The repository marketplace and plugin manifests can be checked independently with `claude plugin validate --strict .` and `claude plugin validate --strict Agentic-qe-plugin`. After an update, refresh the marketplace and plugin in Claude Code with `/plugin marketplace update maf-agentic-qe` and `/plugin update agentic-qe@maf-agentic-qe`, or use the corresponding shell commands. Git-hosted marketplace updates may require a plugin version change; this plugin's version is in `.claude-plugin/plugin.json`.

## What you provide

| Project situation | Required input | QE output scope |
| --- | --- | --- |
| Greenfield Jira | Jira keys/project/JQL and any supplemental specification | E2E cases for approved stories and controls; no code repository required |
| Greenfield BRD | Markdown, TXT, CSV, DOCX or text PDF | Proposed detailed user stories, then approved E2E cases |
| Existing repository | Jira/BRD stories, repository path, and exact committed or working-tree Git diff | Cases only for stories explicitly mapped to changed files |

A Jira BA story usually reads: **“As a [role], I want to [action], so that [business benefit].”** The story intake agent preserves supported wording, extracts acceptance criteria, and assigns stable `STY-*`, `AC-*` and `CTL-*` control IDs. For a BRD, it drafts that same story format from cited sections. Missing roles, outcomes, limits or rules become visible questions; the plugin does not invent them.

Functional requirements use `FR-*` IDs. Explicit technical constraints such as security, accessibility, performance or interoperability use `TR-*` IDs when the source supports them. Both feed E2E planning and case design. Each case has actions, observable expected outcomes, and exact source, story, control, requirement and plan links.

## Start and resume a run

In the project where output should be saved, ask Claude Code:

```text
/agentic-qe:qe-run Greenfield checkout. Read Jira QE-42 and QE-43 plus docs/checkout-brd.md. Draft detailed stories with AC and control IDs, then plan and generate E2E cases.
```

The command saves a new run under `.agentic-qe/runs/<project>-<id>/` in the current working directory. It shows the run path and stops at each required human gate. Approve the actual cited stories and sources in the conversation; an agent response or silence is not approval. To resume, pass the saved run path to `/agentic-qe:qe-run`. Inspect progress at any point:

```text
/agentic-qe:qe-status .agentic-qe/runs/checkout-a1b2c3d4
```

### Seven stages

1. **Intake:** Read selected Jira issues through the configured Atlassian MCP read/search tools, import Jira issue JSON, use Jira REST, or parse local documents. Save source IDs, section locators and hashes. Draft stories and controls. Approve sources and stories.
2. **Scope:** Greenfield proceeds without code. Brownfield captures a selected Git diff, links approved stories to changed paths with reasons, and requires scope approval.
3. **Requirements:** Derive cited functional and technical obligations for the active stories.
4. **Planning:** Cover each active control and requirement with an E2E journey or a named gap.
5. **Design:** Write positive, negative, boundary and technical E2E cases only where evidence supports their conditions and expected results.
6. **Independent review:** Score each active story, requirement, plan item and case for source support (40), traceability (30), E2E quality (20) and clarity (10). Below 95/100 blocks generation until a human approves a specific repair and the revised item is reviewed again. The score is an evidence rubric, not a statistical probability.
7. **Generation:** Validate links, produce the report and matrix, then obtain final approval for the current artifact hashes before export.

The plugin runs the **QE analysis and case-generation workflow**. It does not run the generated scenarios against the target system. This keeps the catalog reviewable before any future automation is built.

## Existing repository: restrict cases to changed code

Ask Claude Code with an explicit diff boundary:

```text
/agentic-qe:qe-run Existing checkout repo at /workspace/shop. Use Jira QE-42 and QE-43 plus docs/checkout-spec.md. Compare main to feature/checkout; analyze only the change set, map affected stories to changed paths, and generate E2E cases only for those stories.
```

For uncommitted local edits, request **working-tree** scope instead and say whether untracked files should count. The committed mode uses the merge base of the selected base and target refs; working-tree mode records staged, unstaged and optionally untracked paths. The capture stores file hashes. The change-scope analyst can add a directly needed related file only with a specific reason and hash; a related file cannot replace the required changed-file link.

For every affected story, the agent records an impact entry in `scope.json`, for example:

```json
{
  "story_id": "STY-1",
  "changed_paths": ["src/checkout/payment.py"],
  "reason": "The changed checkout handler implements this story's payment decision"
}
```

The agent may use `qe.py impact --run RUN --story STY-1 --path src/checkout/payment.py --reason "..."` to record it. Each mapped path must exist in the captured diff, each story must already be approved, and the user approves the resulting scope. **Only mapped stories enter requirements, planning, independent review, the report and the case matrix.** Unmapped Jira stories remain available as source context but cannot receive cases in this brownfield run. If no changed file can be tied to a supplied story, the scope gate stops and asks for the missing story or specification. A changed working tree invalidates its captured scope; recapture and approve it again.

The agent's story-to-code impact judgment is reviewed by a human. The helper enforces the selected IDs, path membership and hashes; it cannot prove semantic equivalence between code and prose.

## Jira, files and MCP

The plugin's `.mcp.json` includes Atlassian's remote `https://mcp.atlassian.com/v2/mcp` endpoint. Connect it through `/mcp` in Claude Code. The intake stage selects available read/search tools that match the saved workflow intent and imports the returned issue fields. It never writes Jira issues. If MCP is unavailable, provide exported issue JSON or use Jira Cloud REST with `JIRA_EMAIL` and `JIRA_API_TOKEN` in your environment. Secrets are never saved in run artifacts.

Local intake supports Markdown, TXT, CSV, DOCX and text PDFs. Scanned PDFs need OCR before intake. Install optional PDF support in the Python environment Claude Code uses:

```sh
python3 -m pip install 'pypdf>=5,<7'
```

On Windows, use `py -3 -m pip` instead. The plugin includes Playwright MCP for optional **read-only observation** of an explicitly approved origin; it is disabled in the default workflow. Observation can refine a case step, but Jira/BRD/contract evidence controls expected outcomes. The plugin never executes generated tests or submits business-changing forms.

`/agentic-qe:qe-workflow` copies the default workflow to a project file. You can change stage prompts, choose installed specialist agents, and select installed MCP servers and tool intents. The seven stage order, approvals, 95/100 floor and generation-only boundary remain fixed. A workflow selects available tools; it cannot install an MCP server or grant permission. See [workflow configuration](references/workflow-guide.md).

## Dashboard and saved artifacts

`/agentic-qe:qe-status RUN` prints a Markdown dashboard **inside Claude Code**: seven stage states, completion percentage, approval gates and stale approvals, source/case counts, changed-file and impacted-story counts, confidence blockers, recent memory and the next action. Each status call also refreshes `dashboard.html`, a responsive static companion with the case catalog and artifact links. It opens no popup and starts no web server. Progress is completed stages out of seven; it is not an ETA or confidence score.

This is comparable to code-modernization's `/modernize-status` artifact-and-next-action report. It is **not** the same as an always-visible interactive progress pane or its interactive architecture map. The HTML page refreshes when a command updates the run; it does not stream live state.

| Run artifact | Purpose |
| --- | --- |
| `sources.json`, `scope.json` | Exact document/Jira evidence and approved Git impact |
| `stories.json`, `requirements.json`, `plan.json`, `cases.json` | Editable, linked draft and final QE artifacts |
| `reviews.json`, `run.json`, `workflow.json` | Independent scores, approvals, stage and workflow snapshot |
| `REPORT.md`, `traceability.csv` | Readable catalog and source/story/change-to-case matrix |
| `dashboard.html`, `memory.md` | Progress companion and BEFORE/AFTER decision/action log |

The helper writes BEFORE/AFTER entries for state-changing commands; agents must add `qe.py note` entries around direct artifact edits and material decisions. Run data may contain private Jira or BRD text, so keep `.agentic-qe/runs/` private. Review/export approval hashes become stale if approved artifacts change.

## Helper CLI and verification

The four Claude Code commands (`qe-run`, `qe-status`, `qe-workflow`, `qe-export`) orchestrate the helper and specialist agents. The helper alone handles intake, state, diff capture, gates, dashboard and export; **Claude agents author the stories, requirements, plan, cases and evidence review**. A minimal manual command sequence is:

```sh
python3 Agentic-qe-plugin/scripts/qe.py init --project "Checkout" --mode greenfield
python3 Agentic-qe-plugin/scripts/qe.py ingest --run RUN docs/checkout-brd.md
python3 Agentic-qe-plugin/scripts/qe.py status --run RUN
python3 Agentic-qe-plugin/scripts/qe.py validate --run RUN
python3 Agentic-qe-plugin/scripts/qe.py generate --run RUN
python3 Agentic-qe-plugin/scripts/qe.py export --run RUN --dest /path/to/output
```

`validate`, `generate` and `export` work only after the intervening agent artifacts, approvals and stages are complete. For a brownfield run, initialize with `--mode brownfield`, capture the scope with `scope --repo REPO --mode committed --base main --target HEAD` (or `--mode working-tree`), then record each impacted story with `impact` before scope approval. Use `python3 Agentic-qe-plugin/scripts/qe.py --help` for all helper subcommands.

The 11 adapted original MAF skills and their seven agent bindings are listed in the [skill inventory](references/skill-inventory.md). The [artifact contract](references/artifact-contract.md), [source policy](references/source-policy.md) and [plan](PLAN.md) describe IDs, citations and gates. The plugin uses standard-library parsing except the optional PDF dependency; it does not install dependencies in the target repository.

Known limits: Jira MCP tool result shapes vary and may need normalization to issue JSON; Jira REST fallback targets Atlassian Cloud. Scanned PDFs need OCR. The CLI records approver names and artifact hashes but cannot authenticate the human identity; use the host's access controls for enterprise approval. The plugin has no shared tenant governance or standalone desktop UI.
