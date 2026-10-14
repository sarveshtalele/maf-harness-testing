---
description: Validate and export an approved Agentic QE E2E case catalog, report and traceability matrix.
argument-hint: "RUN_DIRECTORY and DESTINATION"
---

Read `${CLAUDE_PLUGIN_ROOT}/references/artifact-contract.md`. Run the plugin's `scripts/qe.py validate --run RUN` using `python3` on macOS/Linux or `py -3` on Windows, then inspect `REPORT.md`, `traceability.csv`, `dashboard.html` and the current approvals. Show the report to the user and obtain explicit final approval for the exact current artifacts. Record `approve --run RUN --gate final --by NAME`, then `export --run RUN --dest DESTINATION`. If an artifact changes after approval, repeat validation and approval. Never execute generated test cases.
