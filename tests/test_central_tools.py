"""Central deterministic tools: code intelligence, auto-fixers, build gate and their MAF function-tool wrappers."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import sys
import sysconfig
from pathlib import Path

import pytest

from maf_test_harness import platform_compat
from maf_test_harness.agent_tools import CENTRAL_TOOL_NAMES, STAGE_TOOLS, HarnessToolbox, tool_instructions
from maf_test_harness.autofix import autofix_files, fix_java, fix_python
from maf_test_harness.build_gate import _maven_errors, try_build
from maf_test_harness.code_intel import (
    RepoContext,
    find_symbol,
    installed_packages,
    module_api,
    read_source,
    resolve_import,
    static_gate,
    undefined_python_names,
)
from maf_test_harness.stack import detect_stack


def write(root, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


@pytest.fixture
def py_repo(tmp_path):
    write(
        tmp_path,
        {
            "engine/__init__.py": "",
            "engine/analysis.py": (
                "import json\n\nVERSION = 1\n\n\ndef analyze(data, mode='fast'):\n    import anthropic\n    return json.dumps(data)\n\n\n"
                "class Runner:\n    def go(self, x):\n        return x\n"
            ),
            "skill-pkg/tool.py": "def helper():\n    return 1\n",
            "requirements.txt": "rich>=13\n",
        },
    )
    files = ["engine/__init__.py", "engine/analysis.py", "skill-pkg/tool.py"]
    return RepoContext(root=tmp_path, source_files=files, package_files=["requirements.txt"])


def test_resolve_import_classifies_every_source_and_suggests_repairs(py_repo, tmp_path):
    assert resolve_import(py_repo, "json")["status"] == "stdlib"
    assert resolve_import(py_repo, "engine.analysis") == {
        "module": "engine.analysis",
        "status": "repo",
        "file": "engine/analysis.py",
    }
    assert resolve_import(py_repo, "engine")["status"] == "repo"
    assert resolve_import(py_repo, "rich.console")["status"] == "declared_dependency"
    wrong = resolve_import(py_repo, "app.engine.analysis")
    assert wrong["status"] == "unresolved" and wrong["suggestions"][0]["module"] == "engine.analysis"
    venv = tmp_path / "ws" / ".harness-venv"
    site = (
        venv / "Lib" / "site-packages"
        if platform_compat.IS_WINDOWS
        else venv / "lib" / "python3.12" / "site-packages"
    )
    (site / "PyYAML-6.0.dist-info").mkdir(parents=True)
    (site / "PyYAML-6.0.dist-info" / "top_level.txt").write_text("yaml\n")
    py_repo.workspace = tmp_path / "ws"
    assert resolve_import(py_repo, "yaml")["status"] == "installed"
    assert installed_packages(py_repo)["import_names"] == {"PyYAML": ["yaml"]}


def test_module_api_shows_real_signatures_and_lazy_imports(py_repo):
    api = module_api(py_repo, "engine/analysis.py")
    assert {"line": 6, "signature": "def analyze(data, mode='fast')"} in api["functions"]
    assert api["classes"][0]["methods"] == ["def go(self, x)"]
    assert "anthropic" in api["imported_only_inside_functions"] and "VERSION" in api["module_level_names"]
    assert "not a repository file" in module_api(py_repo, "../etc/passwd")["error"]


def test_find_symbol_and_bounded_read_source(py_repo):
    assert find_symbol(py_repo, "Runner")[0]["path"] == "engine/analysis.py"
    assert find_symbol(py_repo, "no such name") == [] and find_symbol(py_repo, "nothing") == []
    out = read_source(py_repo, "engine/analysis.py", 1, 3)
    assert out["text"].splitlines()[0] == "1: import json" and out["end"] == 3
    assert "not a repository file" in read_source(py_repo, "../../secret")["error"]


def test_static_gate_reports_the_first_defect_for_a_draft_test(py_repo):
    static_gate(
        py_repo,
        "tests/t.py",
        "from engine.analysis import analyze\n\ndef test_a():\n    assert analyze({})\n",
    )
    with pytest.raises(ValueError, match="cannot import name"):
        static_gate(
            py_repo, "tests/t.py", "from engine.analysis import nope\n\ndef test_a():\n    assert nope\n"
        )
    with pytest.raises(ValueError, match="name 'json' is not defined"):
        static_gate(py_repo, "tests/t.py", "def test_a():\n    assert json.dumps({})\n")
    with pytest.raises(ValueError, match="SyntaxError"):
        static_gate(py_repo, "tests/t.py", "def test_a(:\n    pass\n")


def test_static_gate_accepts_an_installed_transitive_dependency_not_directly_declared(py_repo, tmp_path):
    """`langchain` is declared; `langchain_core` is only pulled in transitively, but it really is importable."""
    venv = tmp_path / "ws" / ".harness-venv"
    site = (
        venv / "Lib" / "site-packages"
        if platform_compat.IS_WINDOWS
        else venv / "lib" / "python3.12" / "site-packages"
    )
    (site / "langchain_core-0.1.0.dist-info").mkdir(parents=True)
    (site / "langchain_core-0.1.0.dist-info" / "top_level.txt").write_text("langchain_core\n")
    py_repo.workspace = tmp_path / "ws"
    code = "from langchain_core.language_models.fake import FakeListLLM\n\ndef test_a():\n    assert FakeListLLM\n"
    static_gate(py_repo, "tests/t.py", code)  # would raise "not a declared dependency" without the fix


def test_python_autofix_adds_only_unambiguous_imports_in_the_right_place(py_repo):
    code = '"""doc"""\n# CASE_ID: TC-1\ndef test_a(tmp_path):\n    assert os.sep and json.dumps({}) and MagicMock() and datetime\n'
    fixed, fixes = fix_python(py_repo, "tests/t.py", code)
    assert [f["detail"] for f in fixes] == ["import json", "import os", "from unittest.mock import MagicMock"]
    assert fixed.startswith(
        '"""doc"""\nimport json\nimport os\nfrom unittest.mock import MagicMock\n\n# CASE_ID: TC-1\ndef'
    )
    assert undefined_python_names(fixed) == ["datetime"]  # ambiguous name is left for the model
    compile(fixed, "t.py", "exec")


def test_python_autofix_appends_after_existing_imports_and_rewrites_wrong_module_paths(py_repo):
    code = "import pytest\nfrom app.engine.analysis import analyze\n\ndef test_a():\n    assert analyze(sys.argv)\n"
    fixed, fixes = fix_python(py_repo, "tests/t.py", code)
    assert "from engine.analysis import analyze" in fixed and "import sys" in fixed.splitlines()[2]
    assert {f["kind"] for f in fixes} == {"import_path", "missing_import"}
    static_gate(py_repo, "tests/t.py", fixed)


def test_python_autofix_leaves_unfixable_code_alone(py_repo):
    code = "def test_a():\n    assert mystery()\n"
    assert fix_python(py_repo, "t.py", code) == (code, [])
    assert fix_python(py_repo, "t.py", "def test_a(:\n") == ("def test_a(:\n", [])


@pytest.fixture
def java_repo(tmp_path):
    write(
        tmp_path,
        {
            "src/main/java/org/shop/model/Item.java": "package org.shop.model;\npublic class Item {\n  public Item(String n) {}\n}\n",
            "src/main/java/org/shop/service/Cart.java": "package org.shop.service;\npublic class Cart {}\n",
            "src/main/java/org/other/Cart.java": "package org.other;\npublic class Cart {}\n",
            "pom.xml": "<project><artifactId>junit-jupiter</artifactId></project>",
        },
    )
    files = [
        "src/main/java/org/shop/model/Item.java",
        "src/main/java/org/shop/service/Cart.java",
        "src/main/java/org/other/Cart.java",
    ]
    return RepoContext(root=tmp_path, source_files=files, package_files=["pom.xml"])


def test_java_autofix_adds_jdk_repo_and_junit_imports_but_not_ambiguous_or_same_package(java_repo):
    code = (
        "package org.shop.model;\n\nimport java.util.Map;\n\npublic class ItemTest {\n"
        '  @Test void a() {\n    List<String> xs = new ArrayList<>();\n    Item i = new Item("x");\n'
        '    Cart c = null;\n    Map<String, Integer> m = null;\n    String s = "Date in string";\n'
        "    assertEquals(1, xs.size());\n  }\n}\n"
    )
    fixed, fixes = fix_java(java_repo, "src/test/java/org/shop/model/ItemTest.java", code)
    added = {f["detail"] for f in fixes}
    assert added == {
        "import java.util.ArrayList;",
        "import java.util.List;",
        "import org.junit.jupiter.api.Test;",
        "import static org.junit.jupiter.api.Assertions.*;",
    }  # Item is same package, Cart is ambiguous, Date only appears in a string
    assert "import java.util.Map;\nimport java.util.ArrayList;" in fixed
    assert fixed.count("import java.util.Map;") == 1


def test_java_autofix_uses_a_unique_repo_class_and_skips_annotation_only_path(java_repo):
    code = 'package org.shop.rest;\n\npublic class T {\n  @Path("/x") void a() { Item i = null; }\n}\n'
    fixed, fixes = fix_java(java_repo, "src/test/java/org/shop/rest/T.java", code)
    assert [f["detail"] for f in fixes] == ["import org.shop.model.Item;"]
    assert "java.nio.file.Path" not in fixed
    assert fixed.splitlines()[1] == "" and fixed.splitlines()[2] == "import org.shop.model.Item;"


def test_autofix_files_reports_every_applied_fix(py_repo):
    payload = {
        "files": [
            {"path": "tests/t.py", "content": "def test_a():\n    assert os.sep\n"},
            {"path": "x.md", "content": "hi"},
        ]
    }
    payload, fixes = autofix_files(py_repo, payload)
    assert fixes == [{"path": "tests/t.py", "kind": "missing_import", "detail": "import os"}]
    assert payload["files"][0]["content"].startswith("import os\n")


def _fake_venv(workspace):
    """A venv-shaped launcher that runs the interpreter that has pytest (no network in unit tests)."""
    launcher = Path(platform_compat.venv_python(workspace / ".harness-venv"))
    launcher.parent.mkdir(parents=True)
    if platform_compat.IS_WINDOWS:
        # A real venv python.exe is a binary launcher; CreateProcess can't parse a shebang script, so copy
        # the actual interpreter binary rather than writing a text shim. A bare copy can't find its stdlib
        # on its own -- a real venv's copied exe works because a sibling pyvenv.cfg points it at the base
        # install's "home"; write the same file so this fake venv boots correctly too.
        shutil.copy(sys.executable, launcher)
        (workspace / ".harness-venv" / "pyvenv.cfg").write_text(
            f"home = {Path(sys.executable).parent}\ninclude-system-site-packages = true\n", encoding="utf-8"
        )
        # pytest lives in *this* interpreter's own site-packages, which may itself be a venv nested under
        # a deeper base install (as under `uv run`) -- include-system-site-packages only reaches the final
        # base prefix, not an intermediate venv's site-packages. A .pth file is the portable way to add an
        # arbitrary directory to sys.path regardless of how the venv chain resolves.
        site_packages = workspace / ".harness-venv" / "Lib" / "site-packages"
        site_packages.mkdir(parents=True, exist_ok=True)
        (site_packages / "_fake_venv.pth").write_text(
            sysconfig.get_paths()["purelib"] + "\n", encoding="utf-8"
        )
    else:
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)


def test_build_gate_collects_candidates_reports_errors_and_leaves_no_trace(tmp_path):
    ws = tmp_path / "ws"
    write(ws, {"service.py": "def add(a, b):\n    return a + b\n"})
    _fake_venv(ws)
    files = [
        {
            "path": "tests/test_ok.py",
            "content": "from service import add\n\ndef test_ok():\n    assert add(1, 2) == 3\n",
        },
        {
            "path": "tests/test_bad.py",
            "content": "from service import missing\n\ndef test_bad():\n    assert missing\n",
        },
    ]
    report = try_build(
        detect_stack(ws), ws, files, cache_dir=tmp_path / "cache", out_root=tmp_path / "out", timeout=60
    )
    assert report["status"] == "failed" and report["tool"] == "pytest --collect-only"
    assert [e["file"] for e in report["errors"]] == ["tests/test_bad.py"]
    assert "missing" in report["errors"][0]["message"]
    assert not (ws / "tests").exists() or not list((ws / "tests").glob("*.py"))  # candidates removed again
    ok = try_build(
        detect_stack(ws), ws, files[:1], cache_dir=tmp_path / "cache", out_root=tmp_path / "out2", timeout=60
    )
    assert ok["status"] == "ok" and ok["errors"] == []


def test_build_gate_is_skipped_without_an_environment_or_for_unsupported_ecosystems(tmp_path):
    ws = tmp_path / "ws"
    write(ws, {"service.py": "x = 1\n"})
    files = [{"path": "tests/test_a.py", "content": "def test_a():\n    pass\n"}]
    report = try_build(detect_stack(ws), ws, files, cache_dir=tmp_path / "c", out_root=tmp_path / "o")
    assert report["status"] == "skipped" and "not installed" in report["reason"]


def test_maven_diagnostics_are_mapped_to_generated_files_with_symbol_detail():
    output = (
        "[ERROR] COMPILATION ERROR :\n"
        "[ERROR] /w/src/test/java/org/x/CartTest.java:[12,30] cannot find symbol\n"
        "[ERROR]   symbol:   method doesLoginAlreadyExist(java.lang.String)\n"
        "[ERROR]   location: variable service of type org.x.Service\n"
        "[ERROR] /w/src/main/java/org/x/Other.java:[3,1] broken\n"
    )
    errors = _maven_errors(output, ["src/test/java/org/x/CartTest.java"])
    assert errors == [
        {
            "file": "src/test/java/org/x/CartTest.java",
            "line": 12,
            "message": "cannot find symbol symbol:   method doesLoginAlreadyExist(java.lang.String) "
            "location: variable service of type org.x.Service",
        }
    ]


def call(tool, **kwargs):
    return "".join(c.text for c in asyncio.run(tool.invoke(**kwargs)))


def test_agent_tools_are_real_maf_function_tools_returning_bounded_json(py_repo):
    toolbox = HarnessToolbox(lambda: py_repo, None)
    assert {t.name for t in toolbox.tools_for("test_generation")} == set(STAGE_TOOLS["test_generation"])
    assert toolbox.tools_for("test_planning") == [] and set(STAGE_TOOLS.values()) <= {
        tuple(CENTRAL_TOOL_NAMES)
    } | set(STAGE_TOOLS.values())
    tools = {t.name: t for t in toolbox.tools_for("test_generation")}
    assert all(name in CENTRAL_TOOL_NAMES for name in tools)
    found = json.loads(call(tools["find_symbol"], name="Runner"))
    assert found[0]["path"] == "engine/analysis.py"
    checked = json.loads(call(tools["check_test"], path="t.py", content="def test_a():\n    json.dumps(1)\n"))
    assert checked["ok"] is False and "json" in checked["problem"]
    built = json.loads(call(tools["try_build"], path="t.py", content="x"))
    assert built["status"] == "skipped"
    assert "check_test" in tool_instructions("test_generation") and tool_instructions("test_planning") == ""


def test_tool_instructions_mention_only_the_stage_tools():
    text = tool_instructions("test_design")
    assert "find_symbol" in text and "try_build" not in text and "check_test" not in text
    assert os.environ.get("HARNESS_AGENT_TOOLS", "on") in {"on", "off"}
