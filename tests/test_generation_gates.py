"""Static gates that reject generated tests which cannot pass, before a run is spent on them."""

from __future__ import annotations

from pathlib import Path

import pytest

from maf_test_harness.environment import build_environment_plan, documented_packages, nested_requirements
from maf_test_harness.repo_analyzer import collaborator_signatures
from maf_test_harness.routes import extract_routes, route_prefixes
from maf_test_harness.skill_contracts import check_python_test_hygiene, check_route_literals
from maf_test_harness.stack import detect_stack

APP = "def run():\n    import anthropic\n    return anthropic.Client()\n\n\ndef main():\n    return 1\n"


@pytest.mark.parametrize(
    "call",
    [
        'patch("sys.exit")',
        'patch("reverse_engineer_skill.sys.exit")',
        'mock.patch("sys.exit")',
        'patch.object(sys, "exit")',
        'mocker.patch.object(sys, "exit")',
        'monkeypatch.setattr(sys, "exit", lambda code=0: None)',
        'monkeypatch.setattr("sys.exit", MagicMock())',
    ],
)
def test_mocking_sys_exit_without_a_raising_side_effect_is_rejected(call):
    code = f"import sys\nfrom unittest.mock import patch, MagicMock\n\ndef test_x(monkeypatch):\n    with {call}:\n        pass\n"
    if call.startswith("monkeypatch"):
        code = f"import sys\nfrom unittest.mock import MagicMock\n\ndef test_x(monkeypatch):\n    {call}\n"
    with pytest.raises(ValueError, match="pytest.raises\\(SystemExit\\)"):
        check_python_test_hygiene(code, "tests/t.py")


def test_mocking_sys_exit_with_side_effect_is_the_correct_way_and_passes():
    code = 'import sys\nfrom unittest.mock import patch\n\ndef test_x():\n    with patch("sys.exit", side_effect=SystemExit):\n        pass\n'
    check_python_test_hygiene(code, "tests/t.py")


def test_undefined_names_are_reported_but_fixtures_locals_and_builtins_are_not():
    with pytest.raises(ValueError, match="name 'unittest' is not defined"):
        check_python_test_hygiene("def test_x():\n    m = unittest.mock.MagicMock()\n", "tests/t.py")
    ok = (
        "import os\nimport pytest\n\nclass T:\n    def test_a(self, tmp_path):\n"
        "        try:\n            x = [i for i in range(3)]\n        except OSError as err:\n            print(err, os, x, tmp_path)\n"
        "        f = lambda q, *a, **k: q + len(a)\n        assert f(1) and print and pytest and __file__\n"
    )
    check_python_test_hygiene(ok, "tests/t.py")


def test_a_star_import_disables_the_undefined_name_check():
    check_python_test_hygiene("from os.path import *\n\ndef test_x():\n    assert join('a', 'b')\n", "t.py")


def test_patching_a_lazily_imported_package_is_rejected_with_a_precise_hint():
    files = ["engine/ai_analysis.py", "engine/__init__.py"]
    sources = {"engine/ai_analysis.py": APP, "engine/__init__.py": ""}
    code = 'from unittest.mock import patch\n\ndef test_x():\n    with patch("engine.ai_analysis.anthropic"):\n        pass\n'
    with pytest.raises(ValueError, match="no module-level 'anthropic'"):
        check_python_test_hygiene(code, "tests/t.py", files, read_source=sources.get)


def test_patching_a_locally_assigned_name_hints_the_call_that_produces_it():
    """`vectordb = Chroma.from_documents(...)` is a local: patch the constructor, not the local name it lands in."""
    files = ["app/data_ingestion.py"]
    source = (
        "from langchain_community.vectorstores import Chroma\n\n\n"
        "def load_pdf(path):\n"
        "    vectordb = Chroma.from_documents(documents=[], embedding=None)\n"
        "    vectordb.persist()\n"
    )
    code = (
        "from unittest.mock import patch\n\ndef test_x():\n"
        '    with patch("app.data_ingestion.vectordb"):\n        pass\n'
    )
    with pytest.raises(ValueError, match=r"patch `app\.data_ingestion\.Chroma\.from_documents` instead"):
        check_python_test_hygiene(
            code, "tests/t.py", files, read_source={"app/data_ingestion.py": source}.get
        )


def test_patching_module_level_names_submodules_and_third_parties_is_allowed():
    files = ["engine/ai_analysis.py", "engine/__init__.py"]
    sources = {"engine/ai_analysis.py": APP, "engine/__init__.py": ""}
    code = (
        "from unittest.mock import patch\n\ndef test_x():\n"
        '    with patch("engine.ai_analysis.main"), patch("engine.ai_analysis"), patch("os.getcwd"), '
        'patch("engine.ai_analysis.anthropic", create=True), patch("engine.ai_analysis.input"):\n        pass\n'
    )
    check_python_test_hygiene(code, "tests/t.py", files, read_source=sources.get)


ROUTES = ["GET /rest/items", "POST /rest/items", "GET /rest/items/{id}"]
APP_URL = "http://127.0.0.1:8080/applicationPetstore"


def test_guessed_context_paths_and_unknown_routes_are_rejected_only_on_base_url_lines():
    bad_context = 'var uri = URI.create(BASE_URL + "/applicationPetstore/rest/items");'
    with pytest.raises(ValueError, match="repeats the context path"):
        check_route_literals(bad_context, "T.java", ROUTES, APP_URL)
    with pytest.raises(ValueError, match="not a route this application defines"):
        check_route_literals(
            'var uri = URI.create(BASE_URL + "/petstore/rest/items");', "T.java", ROUTES, APP_URL
        )
    check_route_literals('var uri = URI.create(BASE_URL + "/rest/items/" + id);', "T.java", ROUTES, APP_URL)
    check_route_literals('Path p = Path.of("/tmp/data");', "T.java", ROUTES, APP_URL)
    check_route_literals('var uri = BASE_URL + "/anything";', "T.java", [], APP_URL)


def test_routes_are_extracted_from_jaxrs_spring_flask_and_express(tmp_path):
    (tmp_path / "A.java").write_text('@ApplicationPath("rest")\npublic class A extends Application {}\n')
    (tmp_path / "B.java").write_text(
        '@Path("/items")\npublic class B {\n  @GET\n  public List<Item> all() { return null; }\n'
        '  @GET @Path("/{id : \\\\d+}")\n  public Item one() { return null; }\n  @POST\n  public Response create() { return null; }\n}\n'
    )
    (tmp_path / "S.java").write_text(
        '@RequestMapping("/api")\npublic class S {\n  @GetMapping("/ping")\n  String ping() { return ""; }\n}\n'
    )
    (tmp_path / "f.py").write_text('@app.get("/health")\ndef h(): ...\n\n@app.route("/x")\ndef x(): ...\n')
    (tmp_path / "e.js").write_text("router.post('/users', handler);\napp.get(`/status`, h);\n")
    files = ["A.java", "B.java", "S.java", "f.py", "e.js"]
    routes = extract_routes(tmp_path, files)
    assert "GET /rest/items" in routes and "POST /rest/items" in routes
    assert "GET /rest/items/{id}" in routes
    assert "GET /api/ping" in routes and "GET /health" in routes and "GET /x" in routes
    assert "POST /users" in routes and "GET /status" in routes
    assert "/rest/items" in route_prefixes(routes) and "/api/ping" in route_prefixes(routes)


def test_collaborator_signatures_expose_real_constructors(tmp_path):
    (tmp_path / "Cart.java").write_text(
        "public class Cart {\n  public Cart(Item item,\n      Integer quantity) {}\n  private int hidden;\n"
        "  public Float total() { return 0f; }\n}\n"
    )
    (tmp_path / "Svc.java").write_text("public class Svc { void f() { new Cart(null, 1); } }\n")
    target = "\n--- SOURCE: Svc.java :: f (lines 1-1) ---\nnew Cart(null, 1)\n"
    out = collaborator_signatures(tmp_path, target, ["Cart.java", "Svc.java"])
    assert "public Cart(Item item, Integer quantity)" in out and "total()" in out and "hidden" not in out
    assert "Svc.java" not in out.replace("--- SOURCE", "")


def test_nested_requirements_and_documented_packages_are_supported_by_repository_files(tmp_path):
    (tmp_path / "requirements.txt").write_text("# no deps\n")
    pkg = tmp_path / "pkgs" / "agent"
    pkg.mkdir(parents=True)
    (pkg / "requirements.txt").write_text("anthropic>=0.40.0\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "requirements.txt").write_text("evil\n")
    (tmp_path / "README.md").write_text("pip install anthropic and rich\n")
    assert nested_requirements(tmp_path) == ["pkgs/agent/requirements.txt"]
    needs = [
        {
            "kind": "dependency",
            "how_to_install": "pip install anthropic>=0.40.0",
            "evidence_file": "README.md",
        },
        {"kind": "dependency", "how_to_install": "pip install rich", "evidence_file": "README.md"},
        {
            "kind": "dependency",
            "how_to_install": "pip install ghost",
            "evidence_file": "README.md",
        },  # unsupported
        {
            "kind": "dependency",
            "how_to_install": "pip install evil --index-url http://x",
            "evidence_file": "README.md",
        },
        {
            "kind": "dependency",
            "how_to_install": "pip install anthropic; rm -rf /",
            "evidence_file": "README.md",
        },
        {"kind": "dependency", "how_to_install": "pip install anthropic", "evidence_file": ""},
        {"kind": "configuration", "how_to_install": "pip install anthropic", "evidence_file": "README.md"},
    ]
    assert documented_packages(needs, tmp_path) == ["anthropic>=0.40.0", "rich"]
    plan = build_environment_plan(detect_stack(tmp_path), tmp_path, tmp_path / "cache", 60, needs)
    names = [s["name"] for s in plan["steps"]]
    assert "install-pkgs_agent_requirements.txt" in names and "install-documented-packages" in names
    assert all("/" not in n for n in names)


def test_two_test_methods_in_one_file_are_named_in_the_feedback():
    from maf_test_harness.skill_contracts import validate_generated_files

    code = (
        "// CASE_ID: TC-1\npublic class T {\n  @Test(expected = X.class)\n  public void first() {}\n"
        "  @Test\n  public void second() {}\n}\n"
    )
    payload = {"files": [{"path": "src/test/java/T.java", "content": code, "case_ids": ["TC-1"]}]}
    with pytest.raises(ValueError, match=r"found 2: first, second.*move the extra assertions"):
        validate_generated_files(payload, {"TC-1"})


def test_arquillian_tests_are_rejected_before_a_build_or_run_is_spent_on_them():
    from maf_test_harness.code_intel import RepoContext, static_gate

    ctx = RepoContext(root=Path("."), source_files=[])
    with pytest.raises(ValueError, match="no container"):
        static_gate(
            ctx, "src/test/java/T.java", "import org.jboss.arquillian.junit.Arquillian;\nclass T {}\n"
        )


def test_undeclared_test_libraries_are_listed_as_forbidden_for_the_prompt():
    from maf_test_harness.workflow import _undeclared_libraries

    forbidden = _undeclared_libraries(["junit", "hamcrest-core", "arquillian-junit-container"])
    assert "org.hamcrest" not in forbidden and "org.jboss.arquillian" not in forbidden
    assert "com.jayway.restassured / io.restassured" in forbidden and "org.mockito" in forbidden


def test_a_setup_collection_that_is_never_used_is_rejected():
    bad = "def test_boundary():\n    sections = [1, 2, 3]  # 6 * 10 = 60\n    result = compute()\n    assert result == 60\n"
    with pytest.raises(ValueError, match="'sections' is built but never used"):
        check_python_test_hygiene(bad + "def compute():\n    return 60\n", "t.py")
    used = "def test_a():\n    sections = [1, 2]\n    assert sum(sections) == 3\n"
    check_python_test_hygiene(used, "t.py")
    check_python_test_hygiene("def test_a():\n    _unused = [1]\n    assert True\n", "t.py")


def test_provider_quota_errors_are_shown_readably_and_other_errors_unchanged():
    from maf_test_harness.workflow import friendly_error

    quota = RuntimeError(
        "(\"<class 'X'> service failed: Error code: 429 - {'error': {'message': 'Token quota exceeded - daily token "
        "limit exceeded (25,003,793/25,000,000).', 'code': '429'}}\")"
    )
    text = friendly_error(quota)
    assert text.startswith("The model gateway refused the request (HTTP 429): Token quota exceeded")
    assert "not the harness's daily cap" in text and "<class" not in text
    assert friendly_error(RuntimeError("boom")) == "boom"
    assert friendly_error(RuntimeError("Error code: 500 - {'error': {'message': 'down'}}")).endswith(
        "500: down"
    )
