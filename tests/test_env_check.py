"""env_check: telling a missing declared dependency apart from a defect in a generated test."""

from maf_test_harness.envs import env_check


def test_declared_requirements_reads_requirements_and_pyproject(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "FastAPI==0.1\nuvicorn[standard]==1  # c\n# x\n-e .\n\npython_dotenv>=1\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\ndependencies=["Requests>=2", "chroma-db<1"]\n'
    )
    assert env_check.declared_requirements(tmp_path) == [
        "fastapi",
        "uvicorn",
        "python-dotenv",
        "requests",
        "chroma-db",
    ]


def test_missing_packages_is_declared_minus_installed(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("fastapi\nchromadb\n")
    monkeypatch.setattr(env_check, "installed_distributions", lambda ws: {"fastapi", "pytest"})
    assert env_check.missing_packages(tmp_path) == ["chromadb"]
    monkeypatch.setattr(env_check, "installed_distributions", lambda ws: None)
    assert env_check.missing_packages(tmp_path) == []


def test_module_errors_map_to_missing_distributions():
    out = "E ModuleNotFoundError: No module named 'dotenv'\nE   No module named 'langchain_community.x'"
    assert env_check.missing_module_names(out) == ["dotenv", "langchain_community"]
    missing = ["python-dotenv", "langchain-community"]
    assert env_check.explained_by_missing("dotenv", missing) == "python-dotenv"
    assert env_check.explained_by_missing("langchain_community", missing) == "langchain-community"
    assert env_check.explained_by_missing("engine", missing) is None


def test_build_gate_gap_only_when_every_missing_module_is_a_missing_dependency(tmp_path, monkeypatch):
    from maf_test_harness.tools import build_gate

    monkeypatch.setattr(env_check, "missing_packages", lambda ws: ["fastapi"])
    assert build_gate._environment_gap("No module named 'fastapi'", tmp_path, []) == ["fastapi"]
    assert build_gate._environment_gap("No module named 'engine'", tmp_path, []) == []
    assert (
        build_gate._environment_gap("No module named 'fastapi' No module named 'engine'", tmp_path, []) == []
    )
    assert build_gate._environment_gap("SyntaxError", tmp_path, []) == []
