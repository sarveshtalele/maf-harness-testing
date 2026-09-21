from pathlib import Path

import pytest

from maf_test_harness.repo_analyzer import (
    RepoInputError,
    analyze_repository,
    build_repository_evidence,
    validate_github_url,
)


def test_validate_github_url_accepts_canonical_repo() -> None:
    normalized, owner, repo = validate_github_url("https://github.com/acme/widgets")
    assert normalized == "https://github.com/acme/widgets.git"
    assert owner == "acme"
    assert repo == "widgets"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/acme/widgets",
        "https://gitlab.com/acme/widgets",
        "https://github.com/acme/widgets/issues",
        "https://user:pass@github.com/acme/widgets",
        "file:///tmp/repo",
    ],
)
def test_validate_github_url_rejects_unsafe_or_non_repo_urls(url: str) -> None:
    with pytest.raises(RepoInputError):
        validate_github_url(url)


def test_analyzer_detects_python_tests(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "service.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_service.py").write_text(
        "def test_add(): assert 1 + 1 == 2\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies=["pytest"]\n', encoding="utf-8")

    analysis = analyze_repository(tmp_path, "https://github.com/acme/widgets", "widgets")

    assert analysis.has_tests is True
    assert "tests/test_service.py" in analysis.test_files
    assert analysis.languages["Python"] == 2
    assert "pytest" in analysis.test_frameworks


def test_analyzer_reports_framework_without_test_files(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("fastapi\npytest\n", encoding="utf-8")

    analysis = analyze_repository(tmp_path, "https://github.com/acme/api", "api")

    assert analysis.has_tests is False
    assert "FastAPI" in analysis.frameworks
    assert "pytest" in analysis.test_frameworks
    assert any("no test source files" in note.lower() for note in analysis.notes)


def test_evidence_excludes_and_redacts_secrets(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        'api_key = "super-secret-value"\ndef work(): return 1\n', encoding="utf-8"
    )
    (tmp_path / ".env").write_text("TOKEN=do-not-send\n", encoding="utf-8")

    evidence = build_repository_evidence(tmp_path)

    assert "super-secret-value" not in evidence
    assert "<redacted>" in evidence
    assert "do-not-send" not in evidence


def test_evidence_never_follows_external_symlink(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "private.py"
    outside.write_text("secret_external_content")
    try:
        (repo / "service.py").symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks needs elevated privilege or Developer Mode on this host")
    assert "secret_external_content" not in build_repository_evidence(repo)
    assert analyze_repository(repo, "https://github.com/acme/repo").source_file_count == 0


def test_test_documentation_is_not_test_source(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/README.md").write_text("No tests implemented yet")
    assert not analyze_repository(tmp_path, "https://github.com/acme/repo").has_tests


def test_source_evidence_survives_large_readmes(tmp_path):
    for index in range(6):
        folder = tmp_path / f"package{index}"
        folder.mkdir()
        (folder / "README.md").write_text("Documentation only. " * 500)
    (tmp_path / "README.md").write_text("Overview. " * 800)
    (tmp_path / "reverse_engineer_skill.py").write_text('def main():\n    return "heuristic"\n')
    (tmp_path / "engine").mkdir()
    (tmp_path / "engine/pipeline.py").write_text("def run_pipeline(url):\n    return url\n")
    evidence = build_repository_evidence(tmp_path, max_files=18, max_total_chars=18_000)
    assert "def main()" in evidence
    assert "def run_pipeline(url)" in evidence
    assert len(evidence) <= 18_000
    analysis = analyze_repository(tmp_path, "https://github.com/acme/repo")
    assert "engine/pipeline.py" in analysis.source_files


@pytest.mark.parametrize("url", ["https://github.com:444/acme/repo", "https://github.com/acme/.."])
def test_url_rejects_noncanonical_authority_and_dot_paths(url):
    with pytest.raises(RepoInputError):
        validate_github_url(url)


def test_python_docstrings_do_not_starve_signatures_or_constants(tmp_path):
    (tmp_path / "pipeline.py").write_text(
        '"""'
        + "Long documentation. " * 500
        + '"""\n'
        + 'def run_pipeline(url):\n    """'
        + "More docs. " * 500
        + '"""\n    FILE_CAP = 300\n    return url\n'
    )
    evidence = build_repository_evidence(tmp_path, max_total_chars=1200)
    assert "def run_pipeline(url)" in evidence
    assert "FILE_CAP = 300" in evidence
    assert len(evidence) <= 1200


def test_disabled_spring_plugin_option_is_not_spring_framework(tmp_path):
    (tmp_path / "pom.xml").write_text("<project><springmvc>false</springmvc></project>")
    assert "Spring" not in analyze_repository(tmp_path, "https://github.com/acme/repo").frameworks
