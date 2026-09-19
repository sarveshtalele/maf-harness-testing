---
name: requirement-analysis
description: >
  Pre-development planning skill. Given a free-text feature request or
  change description, deterministically identifies which file(s) need to
  be created or modified, the exact function/class/line to change, an
  existing pattern to follow, plus the security, compliance, and
  formatting/linting rules that apply. Trigger phrases: "I want to add...",
  "implement this feature", "where should I make this change", "what files
  do I need to update for...", "plan this change", "what's the right place
  to add...".
version: 1.0.0
tools:
  - run_in_terminal
  - read_file
  - file_search
  - grep_search
---

# Requirement Analysis Skill

## Purpose

`change-impact-analysis` answers *"I already changed these files — what will break?"*

**This skill answers the question that comes BEFORE that:**
*"I haven't written any code yet — given this requirement, which files do I
need to touch, where exactly, what pattern should I follow, and what
security/compliance/formatting rules apply?"*

You are the AI engine. The Python script in `scripts/` performs deterministic
static analysis (parsing the requirement, indexing the codebase, scoring
candidate files); you provide narrative judgement, the final recommendation,
and any clarifying questions.

No Anthropic API key is required — the host AI (Copilot / Claude / Cursor)
generates all narrative sections.

---

## Step 1 — Get the Requirement

Ask the user for the feature/change description **if not already provided**.

> **"What would you like to implement or change?"**

Capture the requirement as a single free-text string, as close to the
user's own words as possible — the parser extracts entities (names like
`OrderService`, `discount_code`) and keywords (add/modify/remove/fix,
endpoint/model/service/UI/config) directly from this text. Encourage the
user to mention concrete names (class names, field names, file names) if
they know them — this sharply improves the match quality.

---

## Step 2 — Ask for Output Location

> **"Where should I save the implementation plan?"**
> 1. `./requirement-analysis-output/` inside the current directory — *(recommended)*
> 2. Directly in the current directory
> 3. A specific path — type it
> 4. Don't save — just show me the plan in chat (`--json-only`)

Map to flags:
- Option 1 / no answer → omit `--output`
- Option 2 → `--output .`
- Option 3 → `--output <user-supplied-path>`
- Option 4 → add `--json-only`

---

## Step 3 — Run the Analysis Engine

The analysis script is at:
```
.github/skills/requirement-analysis/scripts/requirement_analysis_skill.py
```

**Check Python is available:**
```bash
python --version
```

**Run the engine:**

```bash
# Option 1 — write implementation_plan.md + requirement_analysis.json
python .github/skills/requirement-analysis/scripts/requirement_analysis_skill.py \
  --requirement "Add a discount_code field to the Order model and expose it on the orders API"

# Option 2 — custom output directory
python .github/skills/requirement-analysis/scripts/requirement_analysis_skill.py \
  --requirement "Fix the login endpoint to reject expired tokens" \
  --output ./reports/

# Option 3 — JSON only (no files written)
python .github/skills/requirement-analysis/scripts/requirement_analysis_skill.py \
  --requirement "Add a dark mode toggle to the settings page" --json-only
```

**Quote the requirement exactly as the user phrased it** — pass it as a
single `--requirement "..."` argument.

Wait for the script to complete. Output files produced:

| File | Description |
|------|--------------|
| `implementation_plan.md` | **Primary artifact** — files to update, exact locations, pattern to follow, checklists |
| `requirement_analysis.json` | Machine-readable result |

**If Python or the script is missing**, proceed to [Manual Fallback](#manual-fallback).

---

## Step 4 — Read the Analysis Data

Read the generated files:
```
{output_dir}/implementation_plan.md
{output_dir}/requirement_analysis.json
```

Key fields in `requirement_analysis.json`:

```json
{
  "intent": {
    "action":        "add | modify | remove | fix",
    "target_types":  ["api_endpoint", "database", ...],
    "entities":      ["OrderService", "discount_code"],
    "domain_tags":   ["financial_critical", "security_sensitive", ...]
  },
  "location": {
    "candidates": [
      {
        "path": "src/models/order.py",
        "module_type": "database",
        "score": 28,
        "match_reasons": [...],
        "matched_symbols": [{"name": "Order", "kind": "class", "line": 12, "detail": ""}],
        "suggestion": "Add the new logic near existing related symbol(s): Order (class @ line 12)."
      }
    ],
    "new_file_suggestion": null
  },
  "pattern": {
    "reference_file": "...",
    "reference_symbol": {...},
    "note": "Follow this existing route as a structural template..."
  },
  "security_compliance": [
    {"category": "security", "item": "...", "reference": "OWASP ..."}
  ],
  "formatting": {
    "detected_tools": [{"config_file": "pyproject.toml", "tool": "Black / isort / Ruff (Python)", "command": "black . && isort . && ruff check --fix ."}],
    "candidate_file_styles": {"src/models/order.py": {"indent": "spaces:4", "quote_style": "double", "line_ending": "LF"}}
  }
}
```

---

## Step 5 — Present the Plan

Using `implementation_plan.md` as the source of truth, present a narrative
summary covering:

1. **What was understood** — restate the action, target type(s), and
   entities the engine extracted, so the user can correct you if the
   parsing missed something.
2. **Files to update** — for each candidate (highest score first), state
   the file, the exact symbol/line(s) to change or add near, and *why*
   this file was selected (use `match_reasons`).
3. **If `new_file_suggestion` is present** — explain that no existing file
   was a strong match, and propose the suggested new file path + naming
   convention.
4. **Pattern to follow** — if `pattern` is non-null, point the user at the
   reference file/symbol as a template for the new code.
5. **Security & compliance checklist** — list every item under
   `security_compliance`, grouped by category (security / compliance /
   standard). These are not optional — call out the `security` category
   items explicitly.
6. **Formatting & linting** — list `detected_tools` commands the user
   should run after editing, and note the existing indent/quote style of
   each target file from `candidate_file_styles`.
7. **Low-confidence warning** — if every candidate's `score` is below 6,
   or `candidates` is empty, tell the user explicitly that the match
   confidence is low and ask them to confirm or provide more specific
   names (class/file names) before proceeding.

---

## Step 6 — Offer the Follow-Up

After the user implements the change, recommend running
`change-impact-analysis-skill` on the modified files to compute the
deployment risk score and blast radius:

```bash
python .github/skills/change-impact-analysis/scripts/change_impact_skill.py \
  --changed-files <files from this plan>
```

---

## Manual Fallback (Script Not Found)

If `requirement_analysis_skill.py` is not found or Python is unavailable:

1. From the requirement text, identify: the action (add/modify/remove/fix),
   the type of thing being changed (API endpoint, database model, service,
   UI component, config), and any explicit names mentioned (classes,
   fields, files).
2. Use `file_search` / `grep_search` to find files whose name or contents
   match those names and types.
3. Open the top 2-3 matches and identify the exact function/class to
   change, or the convention to follow for a new one.
4. Use [`references/security-compliance-rules.md`](references/security-compliance-rules.md)
   to manually build the security/compliance checklist for the detected
   target type(s) and domain tag(s).
5. Use `file_search` to find formatter/linter configs
   (`pyproject.toml`, `.eslintrc*`, `.editorconfig`, etc.) and recommend
   the matching command.
6. Use [`templates/implementation_plan.md`](templates/implementation_plan.md)
   to structure the final response.

---

## Notes

- **No API key required** — the host AI is the narrative engine
- **Deterministic** — given the same requirement text and repo state, the
  candidate ranking and checklist are always the same
- **Polyglot** — supports Python, JS/TS, Java, C#, Go
- **No external dependencies** — pure Python standard library
- **Companion skill** — pairs with `change-impact-analysis-skill`
  (plan → implement → impact-analyze → deploy)
- **Script path** — always reference as
  `.github/skills/requirement-analysis/scripts/requirement_analysis_skill.py`
