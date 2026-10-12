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
