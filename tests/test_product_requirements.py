"""Product requirements (tier product): deterministic extraction, verbatim quotes, demotion, old runs."""

import textwrap

from maf_test_harness.qa.oracles import oracle_summary, tier_of
from maf_test_harness.repo.product_requirements import extract_product_requirements, verify_quotes
from maf_test_harness.repo.requirements import requirements_from_facts
from maf_test_harness.skills.contracts import finalize_discovered_requirements


def _fixture(root):
    """Shaped like requirement-analysis-skill: a README property table and feature list, SKILL.md, argparse CLI."""
    (root / "README.md").write_text(
        textwrap.dedent(
            """\
            # Analyzer

            | Property | Meaning |
            |---|---|
            | Deterministic | Same input always gives the same output |
            | Explainable | Every finding cites its evidence |
            | Polyglot | Python, JavaScript and Java declarations are read |

            ## Features

            - **Fast** scanning of every file in the tree
            - Works offline

            The analyzer should report every finding with a line number.
            """
        ),
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(
        "# Analyzer skill\n\nThe skill must refuse unknown inputs.\n", encoding="utf-8"
    )
    (root / "cli.py").write_text(
        textwrap.dedent(
            """\
            import argparse
            parser = argparse.ArgumentParser(description="Analyse a repository deterministically")
            parser.add_argument("--depth", type=int, default=3, help="How many levels to walk")
            parser.add_argument("--format", default="json", help="Output format")
            """
        ),
        encoding="utf-8",
    )
    return ["README.md", "SKILL.md", "cli.py"]


def test_extractor_produces_verified_product_requirements(tmp_path):
    tracked = _fixture(tmp_path)
    items = extract_product_requirements(tmp_path, tracked)
    texts = [item["text"] for item in items]

    assert "The documentation states Deterministic: Same input always gives the same output" in texts
    assert "The documentation lists this feature: Works offline" in texts
    assert "The skill must refuse unknown inputs." in texts
    assert "CLI option --depth defaults to 3. How many levels to walk" in texts
    assert all(item["tier"] == "product" and item["evidence_quotes"] for item in items)
    assert any(item["category"] == "cli" for item in items)
    # Every quote is the verbatim line of its file, so verification keeps every requirement.
    assert verify_quotes(items, tmp_path) == []
    assert all(item["status"] == "verified" for item in items)


def test_unverified_quote_demotes_requirement_to_gap(tmp_path):
    tracked = _fixture(tmp_path)
    items = extract_product_requirements(tmp_path, tracked)
    target = next(item for item in items if item["text"].startswith("The documentation states Explainable"))
    target["evidence_quotes"][0]["quote"] = "Every finding cites a source that does not exist"

    notes = verify_quotes(items, tmp_path)

    assert target["status"] == "gap"
    assert any("demoted to gap" in note and "README.md" in note for note in notes)
    assert sum(item["status"] == "verified" for item in items) == len(items) - 1


def test_product_requirement_without_quote_is_demoted(tmp_path):
    item = {"id": "REQ-X", "tier": "product", "evidence_quotes": []}
    assert verify_quotes([item], tmp_path) and item["status"] == "gap"


def test_deterministic_builder_puts_product_first(tmp_path):
    tracked = _fixture(tmp_path)
    product = extract_product_requirements(tmp_path, tracked)
    analysis = {
        "facts": [
            {
                "source_file": "engine/x.py",
                "line": 113,
                "symbol": "run",
                "confidence": "ast",
                "evidence": "def run(self):",
            }
        ],
        "gaps": [],
        "coverage": {},
        "product_requirements": product,
    }
    draft = requirements_from_facts(analysis, "Analyzer")

    assert [r["tier"] for r in draft["requirements"][: len(product)]] == ["product"] * len(product)
    assert draft["requirements"][-1]["tier"] == "component"
    assert draft["intake"]["product_requirements"] == len(product)
    assert draft["intake"]["represented_declarations"] == 1


def test_old_runs_without_tier_still_load(tmp_path):
    """A requirement written before tiers existed has no tier and no quotes: it reads as component."""
    old = {
        "app_name": "demo",
        "summary": "s",
        "actors": [{"name": "Developer", "description": "d", "source_files": ["service.py"]}],
        "requirements": [
            {
                "text": "add returns the sum",
                "evidence": "service.py defines add",
                "source_files": ["service.py"],
                "category": "functional",
                "actor": "Developer",
            }
        ],
        "acceptance_criteria": [],
        "validation_rules": [],
        "api_contracts": [],
        "environment_needs": [],
        "exclusions": [],
        "assumptions": [],
        "gaps": [],
    }
    model = finalize_discovered_requirements(old, "https://example.test/repo")
    assert tier_of(model["requirement_model"]["requirements"][0]) == "component"
    assert tier_of({}) == "component"
    assert tier_of({"tier": "unknown"}) == "component"
    summary = oracle_summary(
        {"positive_tests": []}, {"requirement_model": {"requirements": [{"id": "REQ-1"}]}}
    )
    assert summary["requirements_by_tier"] == {"product": 0, "component": 1}


# --- caps, exclusions, false positives and speed (review fixes) ---------------------------------------------------

import time  # noqa: E402

from maf_test_harness.repo.product_requirements import MAX_TOTAL, sentences  # noqa: E402


def test_single_long_line_is_skipped_and_finishes_fast(tmp_path):
    (tmp_path / "README.md").write_text("should " * 80_000 + "\n", encoding="utf-8")  # 480 KB, one line
    started = time.monotonic()
    items = extract_product_requirements(tmp_path, ["README.md"])
    assert time.monotonic() - started < 1.0
    assert items == []


def test_table_heavy_readme_is_capped_and_skips_file_index_rows(tmp_path):
    rows = "\n".join(f"| `docs/page{i}.md` | user | Page {i} description |" for i in range(60))
    properties = "\n".join(f"| Property{i} | Documented behaviour number {i} |" for i in range(60))
    (tmp_path / "README.md").write_text(
        f"# Index\n\n| Document | Audience | What |\n|---|---|---|\n{rows}\n\n"
        f"| Property | Meaning |\n|---|---|\n{properties}\n",
        encoding="utf-8",
    )
    items = extract_product_requirements(tmp_path, ["README.md"])
    assert all("docs/page" not in item["text"] for item in items)
    assert len(items) <= 8  # MAX_ROWS_PER_TABLE for the property table; the index table contributes nothing
    assert len(items) <= MAX_TOTAL


def test_total_cap_holds_across_kinds(tmp_path):
    body = "\n".join(f"- Feature number {i} works well here" for i in range(60))
    modal = "\n".join(f"The service should answer request number {i} correctly." for i in range(60))
    (tmp_path / "README.md").write_text(f"## Features\n\n{body}\n\n{modal}\n", encoding="utf-8")
    items = extract_product_requirements(tmp_path, ["README.md"])
    assert len(items) == 40  # bullets 20 + sentences 20


def test_file_index_and_licence_tables_are_skipped_by_column_names(tmp_path):
    (tmp_path / "README.md").write_text(
        "| License | Holder |\n|---|---|\n| MIT | The authors |\n\n"
        "| Platform | Support |\n|---|---|\n| Linux | Full coverage here |\n\n"
        "| Feature | Meaning |\n|---|---|\n| Indexing | Builds the search index |\n",
        encoding="utf-8",
    )
    texts = [i["text"] for i in extract_product_requirements(tmp_path, ["README.md"])]
    assert texts == ["The documentation states Indexing: Builds the search index"]


def test_excluded_paths_and_test_directories_are_never_read(tmp_path):
    for rel in (
        "docs/audit/notes.md",
        "CHANGELOG.md",
        "LICENSE.md",
        "node_modules/pkg/README.md",
        "vendor/x/SKILL.md",
        "docs/PLAN_REVIEW_2026.md",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("The service must never run this statement.\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_cli.py").write_text(
        'import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument("--hidden", default=1)\n',
        encoding="utf-8",
    )
    tracked = [str(p.relative_to(tmp_path)).replace("\\", "/") for p in tmp_path.rglob("*") if p.is_file()]
    assert extract_product_requirements(tmp_path, tracked) == []


def test_roadmap_fences_and_indented_code_are_not_product_statements(tmp_path):
    (tmp_path / "README.md").write_text(
        "## Roadmap\n\nThe service will support plugins in a future release.\n\n"
        "## Usage\n\n~~~bash\nThe fenced example should not be extracted at all.\n~~~\n\n"
        "    indented code: the example must not count here at all\n\n"
        "The tool must report every finding with a line number.\n",
        encoding="utf-8",
    )
    texts = [i["text"] for i in extract_product_requirements(tmp_path, ["README.md"])]
    assert texts == ["The tool must report every finding with a line number."]


def test_sentence_splitter_keeps_filenames_and_abbreviations_whole():
    assert sentences("See README.md. The tool must run e.g. on Linux. It will report findings.") == [
        "See README.md.",
        "The tool must run e.g. on Linux.",
        "It will report findings.",
    ]


def test_cli_prefers_long_flag_and_includes_positionals(tmp_path):
    (tmp_path / "cli.py").write_text(
        "import argparse\np = argparse.ArgumentParser()\n"
        'p.add_argument("target", help="Repository to analyse")\n'
        'p.add_argument("-v", "--verbose", default=False, help="Print more")\n',
        encoding="utf-8",
    )
    texts = [i["text"] for i in extract_product_requirements(tmp_path, ["cli.py"])]
    assert "CLI positional argument target is required. Repository to analyse" in texts
    assert "CLI option --verbose defaults to False. Print more" in texts


def test_readme_and_skill_at_root_come_before_deeper_docs(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text(
        "The guide must describe the deep behaviour clearly.\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(
        "The root readme must describe the main behaviour.\n", encoding="utf-8"
    )
    texts = [i["text"] for i in extract_product_requirements(tmp_path, ["docs/guide.md", "README.md"])]
    assert texts[0].startswith("The root readme")
