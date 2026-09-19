"""
Plan Generator
Renders the final Implementation Plan (Markdown) and the machine-readable
JSON result from the aggregated analysis dictionary produced by
requirement_analysis_skill.py.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


class PlanGenerator:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def generate_markdown(self, result: Dict[str, Any]) -> Path:
        out = self.output_dir / "implementation_plan.md"
        out.write_text(self._build_markdown(result), encoding="utf-8")
        return out

    def generate_json(self, result: Dict[str, Any]) -> Path:
        out = self.output_dir / "requirement_analysis.json"
        out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        return out

    # ------------------------------------------------------------------

    def _build_markdown(self, result: Dict[str, Any]) -> str:
        intent = result["intent"]
        location = result["location"]
        pattern = result.get("pattern")
        checklist = result["security_compliance"]
        formatting = result["formatting"]
        candidates = location.get("candidates", [])
        new_file = location.get("new_file_suggestion")

        lines: List[str] = [
            "# Implementation Plan",
            "",
            f"**Generated:** {self.ts}  ",
            f"**Repository:** `{result.get('repo_path', 'N/A')}`",
            "",
            "## Requirement",
            "",
            f"> {intent['raw_text']}",
            "",
            "---",
            "",
            "## Parsed Intent",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| Action | `{intent['action']}` |",
            f"| Target type(s) | {', '.join(f'`{t}`' for t in intent['target_types']) or '_none detected_'} |",
            f"| Entities | {', '.join(f'`{e}`' for e in intent['entities']) or '_none detected_'} |",
            f"| Domain tag(s) | {', '.join(f'`{d}`' for d in intent['domain_tags']) or '_none_'} |",
            "",
            "---",
            "",
            "## Files To Update",
            "",
        ]

        if candidates:
            for i, c in enumerate(candidates, start=1):
                lines.append(f"### {i}. `{c['path']}` (score: {c['score']}, type: `{c['module_type']}`)")
                lines.append("")
                lines.append(f"**What to do:** {c['suggestion']}")
                lines.append("")
                if c["matched_symbols"]:
                    lines.append("| Symbol | Kind | Line | Detail |")
                    lines.append("|--------|------|------|--------|")
                    for s in c["matched_symbols"]:
                        lines.append(f"| `{s['name']}` | {s['kind']} | {s['line']} | {s.get('detail') or '—'} |")
                    lines.append("")
                lines.append("<details><summary>Why this file was selected</summary>")
                lines.append("")
                for reason in c["match_reasons"]:
                    lines.append(f"- {reason}")
                lines.append("")
                lines.append("</details>")
                lines.append("")
        else:
            lines.append("_No existing file scored as a strong match for this requirement._")
            lines.append("")

        if new_file:
            lines += [
                "### New File Suggestion",
                "",
                f"**Suggested path:** `{new_file['suggested_path']}`  ",
                f"**Module type:** `{new_file['module_type']}`  ",
                f"**Naming convention reference:** `{new_file['naming_convention_example']}`",
                "",
                new_file["note"],
                "",
            ]

        lines += ["---", "", "## Pattern To Follow", ""]
        if pattern:
            ref_sym = pattern["reference_symbol"]
            lines.append(pattern["note"])
            lines.append("")
            lines.append(f"```\nFile: {pattern['reference_file']}")
            lines.append(f"Symbol: {ref_sym['name']} ({ref_sym['kind']}) at line {ref_sym['line']}")
            if ref_sym.get("detail"):
                lines.append(f"Detail: {ref_sym['detail']}")
            lines.append("```")
        else:
            lines.append("_No structural template found — implement following the conventions of the target file(s) above._")
        lines.append("")

        lines += ["---", "", "## Security & Compliance Checklist", ""]
        for category in ("security", "compliance", "standard"):
            cat_items = [c for c in checklist if c["category"] == category]
            if not cat_items:
                continue
            lines.append(f"### {category.capitalize()}")
            lines.append("")
            for item in cat_items:
                ref = f" _(ref: {item['reference']})_" if item.get("reference") else ""
                lines.append(f"- [ ] {item['item']}{ref}")
            lines.append("")

        lines += ["---", "", "## Formatting & Linting", ""]
        tools = formatting.get("detected_tools", [])
        if tools:
            lines.append("Detected configuration in this repo:")
            lines.append("")
            lines.append("| Config File | Tool | Run Before Commit |")
            lines.append("|-------------|------|--------------------|")
            for t in tools:
                lines.append(f"| `{t['config_file']}` | {t['tool']} | `{t['command']}` |")
            lines.append("")
        else:
            lines.append("_No formatter/linter config detected — match the existing style of each file below._")
            lines.append("")

        styles = formatting.get("candidate_file_styles", {})
        if styles:
            lines.append("Existing style of target file(s):")
            lines.append("")
            lines.append("| File | Indent | Quote Style | Line Ending |")
            lines.append("|------|--------|-------------|-------------|")
            for path, style in styles.items():
                lines.append(f"| `{path}` | {style['indent']} | {style['quote_style']} | {style['line_ending']} |")
            lines.append("")

        lines += [
            "---",
            "",
            "## Next Steps",
            "",
            "1. Implement the change in the file(s) listed under **Files To Update**, following **Pattern To Follow**.",
            "2. Work through the **Security & Compliance Checklist** above.",
            "3. Run the formatter/linter commands listed under **Formatting & Linting**.",
            "4. Run `change-impact-analysis-skill` on the modified files to assess deployment risk before merging.",
            "",
            "---",
            "_Plan generated by **Requirement Analysis Skill v1.0.0**_",
        ]

        return "\n".join(lines)
