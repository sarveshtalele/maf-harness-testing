---
description: Show Agentic QE progress, blockers, source counts and next action for a saved run in Claude Code.
argument-hint: "RUN_DIRECTORY"
---

Resolve `$ARGUMENTS` as the selected run directory and quote it safely as one shell argument; never paste untrusted text directly into a command. Run the plugin's `scripts/qe.py status --run RUN` with `python3` on macOS/Linux or `py -3` on Windows. The CLI prints the seven-stage Markdown dashboard with evidence counts, review blockers, recent memory, completed percentage and next action. State that percentage is stages completed out of seven and show remaining stage count, not an invented time ETA. Link to the saved `dashboard.html`. Do not open a browser or popup.
