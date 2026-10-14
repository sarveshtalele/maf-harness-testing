"""Synthetic end-to-end journeys and trust-boundary checks for the plugin helper."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from git_scope import capture
from qe import (
    approval,
    approve_repair,
    complete_stage,
    create,
    dashboard,
    export,
    generate,
    logged_action,
    memory_line,
    sha,
    validate_reviews,
    validate_scope,
    validate_workflow,
    write_json,
)
from sources import fetch_jira, ingest_files, ingest_jira_issues

ROOT = Path(__file__).resolve().parents[1]
QUOTE = "A customer signs in with email and password to access their account."
REF = {"source_id": "DOC-1", "locator": "line:1", "quote": QUOTE}


class PluginSkillWiring(unittest.TestCase):
    def test_existing_maf_skills_are_native_and_bound_to_agents(self):
        expected = {
            "reverse-engineering",
            "repository-requirements",
            "spec-authoring",
            "requirement-analysis",
            "test-planning",
            "test-design-generator",
            "traceability-generator",
            "coverage-analyzer",
            "api-contract-analyzer",
            "test-data-generator",
            "playwright-test-generator",
        }
        agent_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "agents").glob("*.md"))
        for name in expected:
            skill = ROOT / "skills" / name / "SKILL.md"
            self.assertTrue(skill.is_file(), name)
            content = skill.read_text(encoding="utf-8")
            self.assertIn(f"name: {name}", content)
            self.assertIn(f"agentic-qe:{name}", agent_text)
        for excluded in ("generic-test-generator", "junit-generator"):
            self.assertFalse((ROOT / "skills" / excluded).exists())


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def artifacts(run: Path) -> dict:
    story = {
        "id": "STY-1",
        "user_story": "As a customer, I want to sign in with email and password, so that I can access my account",
        "source_refs": [REF],
        "acceptance_criteria": [{"id": "AC-1", "control_id": "CTL-1", "text": QUOTE, "source_refs": [REF]}],
    }
    requirement = {
        "id": "FR-1",
        "type": "functional",
        "description": QUOTE,
        "story_ids": ["STY-1"],
        "control_ids": ["CTL-1"],
        "source_refs": [REF],
    }
    technical = {
        "id": "TR-1",
        "type": "technical",
        "description": "Sign-in must be observable end to end",
        "story_ids": ["STY-1"],
        "control_ids": ["CTL-1"],
        "source_refs": [REF],
    }
    plan = {
        "id": "PLN-1",
        "objective": "Verify customer access after sign-in",
        "requirement_ids": ["FR-1", "TR-1"],
        "control_ids": ["CTL-1"],
        "source_refs": [REF],
    }
    case = {
        "id": "TC-1",
        "level": "e2e",
        "title": "Customer signs in",
        "steps": [{"action": "Enter email and password", "expected": "Account opens"}],
        "expected_outcome": "Customer accesses their account",
        "requirement_ids": ["FR-1", "TR-1"],
        "control_ids": ["CTL-1"],
        "plan_ids": ["PLN-1"],
        "source_refs": [REF],
    }
    for filename, key, records in (
        ("stories.json", "stories", [story]),
        ("requirements.json", "requirements", [requirement, technical]),
        ("plan.json", "items", [plan]),
        ("cases.json", "cases", [case]),
    ):
        write_json(run / filename, {key: records})
    return {item["id"]: item for item in (story, requirement, technical, plan, case)}


class RunJourney(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = create("Customer account", "greenfield", ROOT / "workflows" / "default.json", self.root)
        write_json(
            self.run / "sources.json",
            {
                "sources": [
                    {
                        "id": "DOC-1",
                        "type": "document",
                        "name": "brd.md",
                        "sha256": hashlib.sha256(QUOTE.encode()).hexdigest(),
                        "sections": 1,
                    }
                ],
                "sections": [{"source_id": "DOC-1", "locator": "line:1", "title": "BRD", "text": QUOTE}],
                "warnings": [],
            },
        )
        self.subjects = artifacts(self.run)
        approval(self.run, "sources", "Product owner")
        approval(self.run, "stories", "Product owner")
        for stage in ("intake", "scope", "requirements", "planning", "design"):
            complete_stage(self.run, stage)

    def reviews(self, case_score=20):
        records = []
        for identifier, item in self.subjects.items():
            records.append(
                {
                    "subject_id": identifier,
                    "subject_sha256": sha(item),
                    "round": 0,
                    "scores": {
                        "source_support": 40,
                        "traceability": 30,
                        "e2e_quality": case_score if identifier == "TC-1" else 20,
                        "clarity": 10,
                    },
                    "verdict": "approved" if identifier != "TC-1" or case_score >= 15 else "revise",
                    "reason": "Verified cited source and links"
                    if case_score >= 15
                    else "Expected result needs clarification",
                    "reviewer": "Independent reviewer",
                    "at": "2026-10-14T00:00:00Z",
                }
            )
        write_json(self.run / "reviews.json", {"reviews": records})

    def test_greenfield_to_approved_export(self):
        self.reviews()
        self.assertTrue(validate_reviews(self.run)["ok"])
        complete_stage(self.run, "review")
        generate(self.run)
        self.assertIn("100%", dashboard(self.run))
        with self.assertRaisesRegex(ValueError, "final approval"):
            export(self.run, self.root / "export")
        approval(self.run, "final", "Product owner")
        destination = self.root / "export"
        export(self.run, destination)
        self.assertIn("TC-1", (destination / "traceability.csv").read_text())
        self.assertIn("TC-1", (destination / "REPORT.md").read_text())
        self.assertIn("Customer account", (destination / "dashboard.html").read_text())
        self.assertIn("Approved for export", (destination / "dashboard.html").read_text())
        self.assertIn("BEFORE | initialize", (destination / "memory.md").read_text())
        (self.run / "REPORT.md").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "final approval"):
            export(self.run, destination)

    def test_low_score_requires_human_repair_and_new_review(self):
        self.reviews(case_score=10)
        self.assertFalse(validate_reviews(self.run)["ok"])
        with self.assertRaisesRegex(ValueError, "Confidence gate"):
            complete_stage(self.run, "review")
        approve_repair(self.run, "TC-1", "Product owner")
        self.subjects["TC-1"]["expected_outcome"] = "Customer reaches their account after valid sign-in"
        write_json(self.run / "cases.json", {"cases": [self.subjects["TC-1"]]})
        records = json.loads((self.run / "reviews.json").read_text())["reviews"]
        records.append(
            {
                "subject_id": "TC-1",
                "subject_sha256": sha(self.subjects["TC-1"]),
                "round": 1,
                "scores": {"source_support": 40, "traceability": 30, "e2e_quality": 18, "clarity": 10},
                "verdict": "approved",
                "reason": "Repaired expected outcome and checked source",
                "reviewer": "Independent reviewer",
                "at": "2026-10-14T00:01:00Z",
            }
        )
        write_json(self.run / "reviews.json", {"reviews": records})
        self.assertTrue(validate_reviews(self.run)["ok"])

    def test_stale_approval_and_invalid_links_block(self):
        self.reviews()
        story_file = self.run / "stories.json"
        stories = json.loads(story_file.read_text())
        stories["stories"][0]["user_story"] += " now"
        write_json(story_file, stories)
        with self.assertRaisesRegex(ValueError, "approvals are missing or stale"):
            validate_reviews(self.run)
        approval(self.run, "stories", "Product owner")
        with self.assertRaisesRegex(ValueError, "review is stale"):
            result = validate_reviews(self.run)
            if not result["ok"]:
                raise ValueError("review is stale")

    def test_before_after_memory_records_failure_and_decision(self):
        memory_line(self.run, "BEFORE", "planning-decision", "Choose account access journey")
        memory_line(self.run, "AFTER", "planning-decision", "Journey linked to account control")
        with (
            self.assertRaisesRegex(ValueError, "intentional"),
            logged_action(self.run, "stage-gate", "Try incomplete artifact gate"),
        ):
            raise ValueError("intentional")
        record = (self.run / "memory.md").read_text(encoding="utf-8")
        self.assertIn("BEFORE | planning-decision", record)
        self.assertIn("AFTER | planning-decision", record)
        self.assertIn("AFTER | stage-gate | Failed: ValueError", record)


class IntakeAndScope(unittest.TestCase):
    def test_cli_status_and_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            command = [sys.executable, str(ROOT / "scripts" / "qe.py")]
            initialized = subprocess.run(
                command + ["init", "--project", "CLI sample", "--mode", "greenfield", "--output-root", temp],
                check=True,
                capture_output=True,
                text=True,
            )
            run = Path(initialized.stdout.strip())
            subprocess.run(
                command
                + [
                    "note",
                    "--run",
                    str(run),
                    "--phase",
                    "before",
                    "--stage",
                    "intake",
                    "--summary",
                    "Review selected BRD section",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            status = subprocess.run(
                command + ["status", "--run", str(run)], check=True, capture_output=True, text=True
            )
            self.assertIn("| Intake | pending |", status.stdout)
            self.assertIn("**Progress:** 0%", status.stdout)
            failed = subprocess.run(
                command + ["approve", "--run", str(run), "--gate", "final", "--by", "Owner"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, failed.returncode)
            self.assertIn("AFTER | human-approval | Failed: ValueError", (run / "memory.md").read_text())

    def test_markdown_csv_docx_and_jira_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "brd.md").write_text("# Access\n" + QUOTE + "\n", encoding="utf-8")
            (root / "stories.csv").write_text("story,priority\nCustomer signs in,High\n", encoding="utf-8")
            xml = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Customer signs in</w:t></w:r></w:p></w:body></w:document>'
            with zipfile.ZipFile(root / "brd.docx", "w") as archive:
                archive.writestr("word/document.xml", xml)
            result = ingest_files([root / "brd.md", root / "stories.csv", root / "brd.docx"])
            self.assertEqual(3, len(result["sources"]))
            self.assertEqual(
                ["line:1", "row:2", "paragraph:1"], [item["locator"] for item in result["sections"]]
            )
            issue = {
                "key": "QE-42",
                "fields": {
                    "summary": "Sign in",
                    "description": {
                        "type": "doc",
                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": QUOTE}]}],
                    },
                    "updated": "2026-10-14",
                },
            }
            imported = ingest_jira_issues({"issues": [issue]})
            self.assertEqual("JIRA-QE-42", imported["sources"][0]["id"])
            self.assertIn(QUOTE, imported["sections"][0]["text"])

    def test_jira_rest_pagination_and_url_restriction(self):
        calls = []

        def issue(key):
            return {"key": key, "fields": {"summary": key, "description": "Story", "updated": "today"}}

        def request(method, url, headers, payload):
            calls.append((method, url, payload))
            if method == "GET":
                return issue("QE-1")
            return {
                "issues": [issue("QE-2" if not payload.get("nextPageToken") else "QE-3")],
                "nextPageToken": "page-2" if not payload.get("nextPageToken") else None,
            }

        result = fetch_jira(
            "https://team.atlassian.net", "user@example.com", "secret", ["QE-1"], "project = QE", request
        )
        self.assertEqual(3, len(result["sources"]))
        self.assertEqual(3, len(calls))
        with self.assertRaisesRegex(ValueError, "site must"):
            fetch_jira(
                "https://team.atlassian.net/redirect",
                "user@example.com",
                "secret",
                ["QE-1"],
                request_fn=request,
            )

    def test_committed_scope_hashes_target_and_worktree_staleness(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            git(repo, "init", "-q")
            git(repo, "config", "user.name", "Tester")
            git(repo, "config", "user.email", "test@example.com")
            file = repo / "app.txt"
            file.write_text("base", encoding="utf-8")
            git(repo, "add", "app.txt")
            git(repo, "commit", "-qm", "base")
            base = git(repo, "rev-parse", "HEAD")
            file.write_text("committed", encoding="utf-8")
            git(repo, "add", "app.txt")
            git(repo, "commit", "-qm", "change")
            file.write_text("unstaged", encoding="utf-8")
            committed = capture(repo, "committed", base)
            self.assertEqual(
                hashlib.sha256(b"committed").hexdigest(), committed["changed_files"][0]["sha256"]
            )
            git(repo, "add", "app.txt")
            file.write_text("after-stage", encoding="utf-8")
            working = capture(repo, "working-tree")
            by_group = {entry["group"]: entry["sha256"] for entry in working["changed_files"]}
            self.assertEqual(hashlib.sha256(b"unstaged").hexdigest(), by_group["staged"])
            self.assertEqual(hashlib.sha256(b"after-stage").hexdigest(), by_group["unstaged"])
            run = create("Brownfield", "brownfield", ROOT / "workflows" / "default.json", Path(temp))
            write_json(run / "scope.json", working)
            validate_scope(run)
            file.write_text("changed again", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed after capture"):
                validate_scope(run)

    def test_brownfield_journey_to_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init", "-q")
            git(repo, "config", "user.name", "Tester")
            git(repo, "config", "user.email", "test@example.com")
            (repo / "auth.py").write_text("def sign_in(): return False\n", encoding="utf-8")
            git(repo, "add", "auth.py")
            git(repo, "commit", "-qm", "base")
            base = git(repo, "rev-parse", "HEAD")
            (repo / "auth.py").write_text("def sign_in(): return True\n", encoding="utf-8")
            git(repo, "add", "auth.py")
            git(repo, "commit", "-qm", "change")
            run = create("Account change", "brownfield", ROOT / "workflows" / "default.json", root)
            write_json(
                run / "sources.json",
                {
                    "sources": [
                        {
                            "id": "DOC-1",
                            "type": "document",
                            "name": "brd.md",
                            "sha256": hashlib.sha256(QUOTE.encode()).hexdigest(),
                            "sections": 1,
                        }
                    ],
                    "sections": [{"source_id": "DOC-1", "locator": "line:1", "title": "BRD", "text": QUOTE}],
                    "warnings": [],
                },
            )
            subjects = artifacts(run)
            write_json(run / "scope.json", capture(repo, "committed", base))
            for gate in ("sources", "stories", "scope"):
                approval(run, gate, "Product owner")
            for stage in ("intake", "scope", "requirements", "planning", "design"):
                complete_stage(run, stage)
            reviews = [
                {
                    "subject_id": identifier,
                    "subject_sha256": sha(item),
                    "round": 0,
                    "scores": {"source_support": 40, "traceability": 30, "e2e_quality": 20, "clarity": 10},
                    "verdict": "approved",
                    "reason": "Citations and bounded diff checked",
                    "reviewer": "Independent reviewer",
                    "at": "2026-10-14T00:00:00Z",
                }
                for identifier, item in subjects.items()
            ]
            write_json(run / "reviews.json", {"reviews": reviews})
            complete_stage(run, "review")
            generate(run)
            approval(run, "final", "Product owner")
            export(run, root / "export")
            self.assertTrue((root / "export" / "scope.json").exists())

    def test_workflow_cannot_relax_gate(self):
        workflow = json.loads((ROOT / "workflows" / "default.json").read_text())
        validate_workflow(workflow)
        workflow["confidence_floor"] = 90
        with self.assertRaisesRegex(ValueError, "fixed at 95"):
            validate_workflow(workflow)


if __name__ == "__main__":
    unittest.main()
