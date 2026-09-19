"""Vendor selected Agent Skills from an upstream skills repository and write ``skills/SKILLS.lock.json``.

    git clone https://github.com/shivswami/awesome-agent-skills.git /tmp/awesome-agent-skills
    uv run python scripts/vendor_skills.py --source /tmp/awesome-agent-skills

What it does (all offline after the clone):
  1. copies the selected skill directories verbatim (scripts, references, assets, evals, cards) into ``skills/``;
  2. audits each copy with the upstream ``evaluator-skill`` (static AST/taint security + quality, ``--no-llm``);
  3. records the upstream URL and commit, a SHA-256 for every vendored file, the audit result, and the allow-list
     of *reviewed* scripts (name + hash). The harness refuses to load an upstream skill whose files differ from
     the lock and refuses to run any script that is not on the allow-list.

Reviewing a script means reading it; adding one to REVIEWED_SCRIPTS is a code-review decision, not a config flag.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"

# Selected by what the harness needs (see docs/ARCHITECTURE.md §6.3), not "everything in the repo".
SELECTED = [
    "api-contract-analyzer",  # requirements: explicit API contracts when a spec exists
    "test-data-generator",  # design/generation: deterministic fixtures, seeds, payloads
    "junit-generator",  # generation: JUnit conventions for Java/Kotlin repositories
    "playwright-test-generator",  # generation: resilient browser E2E for UI cases
    "traceability-generator",  # QA: requirement -> test -> artifact matrix contract + validator
    "coverage-analyzer",  # QA: coverage/gap report contract
]
# Reviewed by reading the source: pure functions of their arguments/inputs; no network, no subprocess, no writes
# outside stdout. "model" scripts may be called by the agent through MAF run_skill_script; "host" scripts are
# only ever run by the harness itself on its own artifacts.
REVIEWED_SCRIPTS = {
    "test-data-generator": {"model": ["scripts/deterministic_helpers.py"], "host": ["scripts/validate_fixtures.py"]},
    "traceability-generator": {"model": [], "host": ["scripts/validate_traceability.py"]},
    "api-contract-analyzer": {"model": [], "host": ["scripts/validate_contract.py"]},
}
# Reviewed and deliberately NOT enabled: junit-generator/scripts/generate_junit.js (writes files; its output
# contains placeholders the harness forbids) and coverage-analyzer/scripts/run-coverage.js (execSync of npx).
SKIP_NAMES = {".git", "__pycache__", "node_modules", ".DS_Store"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_files(directory: Path) -> dict[str, str]:
    return {
        p.relative_to(directory).as_posix(): sha256(p)
        for p in sorted(directory.rglob("*"))
        if p.is_file() and not any(part in SKIP_NAMES for part in p.relative_to(directory).parts)
    }


def audit(evaluator: Path, skill_dir: Path, out: Path) -> dict:
    """Run the upstream evaluator (offline, no LLM) and keep the numbers that matter."""
    out.mkdir(parents=True, exist_ok=True)
    run = [sys.executable, str(evaluator / "scripts" / "run_evaluation.py"), "--skill", str(skill_dir),
           "--no-llm", "--format", "json", "--output", str(out)]
    subprocess.run(run, capture_output=True, text=True, timeout=300, check=False)
    report = next(iter(out.glob("*.json")), None)
    for candidate in out.glob(f"{skill_dir.name}*.json"):
        report = candidate
    if report is None:
        return {"status": "not_run"}
    data = json.loads(report.read_text(encoding="utf-8"))
    security = data.get("security", {})
    return {"tool": "evaluator-skill (NVIDIA SkillSpector rules, --no-llm)", "score": data.get("overall_score"),
            "gate": data.get("gate_decision"), "security_findings": len(security.get("findings", [])),
            "security_risk": security.get("risk_severity")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="Clone of the upstream skills repository")
    parser.add_argument("--url", default="https://github.com/shivswami/awesome-agent-skills.git")
    parser.add_argument("--audit-dir", type=Path, default=ROOT / ".skill-audit")
    args = parser.parse_args()
    source = args.source.resolve()
    commit = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    branch = subprocess.run(["git", "-C", str(source), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    evaluator = source / "Skills" / "evaluator-skill"
    lock = {"upstream": {"url": args.url, "branch": branch, "commit": commit}, "skills": {}, "reviewed_scripts": {}}
    for name in SELECTED:
        origin = source / "Skills" / name
        if not (origin / "SKILL.md").is_file():
            raise SystemExit(f"Selected skill missing upstream: {name}")
        target = SKILLS / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(origin, target, ignore=shutil.ignore_patterns(*SKIP_NAMES))
        entry = {"origin": "upstream", "path": f"Skills/{name}", "files": package_files(target)}
        entry["audit"] = audit(evaluator, target, args.audit_dir / name) if evaluator.is_dir() else {"status": "not_run"}
        lock["skills"][name] = entry
        for tier, scripts in REVIEWED_SCRIPTS.get(name, {}).items():
            for rel in scripts:
                lock["reviewed_scripts"].setdefault(name, {})[rel] = {"tier": tier, "sha256": entry["files"][rel]}
        print(f"vendored {name}: {len(entry['files'])} files, audit {entry['audit']}")
    (SKILLS / "SKILLS.lock.json").write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
