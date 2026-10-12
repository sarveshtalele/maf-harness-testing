import asyncio
import json

from maf_test_harness.repo.analyzer import _DEFINITION, _brace_body_end, extract_target_sources
from maf_test_harness.workflow_steps.test_review import review_generated_tests


def _name(line):
    m = _DEFINITION.match(line)
    return next((g for g in m.groups() if g), None) if m else None


def _java(body_lines=150):
    body = "\n".join(
        f'        total += {i}; // brace }} in comment\n        s = "}}{{";' for i in range(body_lines // 2)
    )
    return (
        "public class Shop {\n"
        "    public void setPrice(double p) {\n"
        '        String t = """\n        }\n        """;\n'
        f"{body}\n"
        "        char c = '}';\n    }\n"
        "    public double afterTarget() { return 1; }\n"
        "}\n"
    )


def test_brace_body_end_ignores_braces_in_literals_and_stops_at_method_end():
    lines = _java().splitlines()
    end = _brace_body_end(lines, 1)
    assert lines[end - 1] == "    }" and "afterTarget" in lines[end]
    assert end > 150


def test_bodiless_declarations_end_on_their_line_and_unknown_shapes_fall_back():
    lines = ["    abstract void run();", *[f"x{i}" for i in range(100)]]
    assert _brace_body_end(lines, 0) == 1  # abstract method: the declaration is the whole body
    arrow = [
        "const mcpUrl = () => (typeof window !== 'undefined' ? `${o}/mcp` : '/mcp');",
        "type Stats = {",
        "};",
    ]
    assert _brace_body_end(arrow, 0) == 1
    assert (
        _brace_body_end(["function f({ a, b }: P) {", "  return a;", "}", "x"], 0) == 3
    )  # destructured params
    assert _brace_body_end([f"x{i}" for i in range(100)], 0) == 70  # no brace or ';' in the first lines


def test_definition_regex_variants():
    assert _name("    public async Task<int> Foo(int a)") == "Foo"
    assert _name("  public static Map<String, List<Foo>> build() {") == "build"
    assert _name("    private T[] items(int n) {") == "items"
    assert _name("  async fetchAll(id: string): Promise<Foo[]> {") == "fetchAll"
    assert _name("export const load = async (a) => {") == "load"
    assert _name("    override fun total(x: Int): Int {") == "total"
    assert _name("def thing(a):") == "thing"
    for line in (
        "if (x) {",
        "    } else if (y) {",
        "while (a) {",
        "return foo(x);",
        "x = new Foo(1);",
        "catch (E e) {",
    ):
        assert _name(line) is None, line


def test_extract_target_sources_returns_whole_java_method(tmp_path):
    (tmp_path / "Item.java").write_text(_java().replace("\n", "\r\n"), encoding="utf-8", newline="")
    out = extract_target_sources(tmp_path, ["Item.java setPrice"], ["Item.java"])
    assert "total += 74;" in out and "afterTarget" not in out


def test_reviewer_prompt_has_full_target_method_for_large_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    filler = "\n".join(f"    int f{i} = {i};" for i in range(800))
    (repo / "Item.java").write_text(
        f"public class Item {{\n{filler}\n    public void setPrice(double p) {{\n"
        "        price = p; // MARK_END\n    }\n}\n",
        encoding="utf-8",
    )
    prompts = []

    class Host:
        run_dir = tmp_path

        async def _call_model(self, **kw):
            prompts.append(json.loads(kw["prompt"]))
            review = {
                "case_id": "c1",
                "scores": {
                    "source_and_api_match": 25,
                    "assertion_checks_real_behavior": 30,
                    "fixture_validity": 20,
                    "isolation_and_determinism": 15,
                    "traceability": 10,
                },
                "confidence": 90,
                "verdict": "supported",
                "reason": "Source supports the setter assertion.",
                "source_files": ["Item.java"],
            }
            return {"status": "completed", "artifact": {"reviews": [review]}}

        def audit(self, *a, **k):
            pass

        def save(self):
            pass

    state = {"artifacts": {"requirements": {}, "repo_analysis": {"source_files": ["Item.java"]}}}
    cases = [{"id": "c1", "target": "Item.java setPrice"}]
    candidate = {
        "files": [
            {
                "path": "tests/test_item.py",
                "case_ids": ["c1"],
                "content": "item.setPrice(2)\nassertEquals(2, item.price)\n",
            }
        ]
    }
    asyncio.run(review_generated_tests(Host(), state, candidate, cases, 1))
    assert "MARK_END" in prompts[0]["target_source"]["c1"]
    assert "authoritative complete implementation" in prompts[0]["assignment"]


def test_bare_symbol_targets_resolve_to_their_defining_file(tmp_path):
    from maf_test_harness.repo.analyzer import extract_target_sources

    (tmp_path / "app").mkdir()
    (tmp_path / "app/page.tsx").write_text(
        "export default function Page() { return null }\n\nfunction ConnectorCards({ a }: P) {\n  return a;\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "graph.py").write_text(
        "class DependencyGraph:\n    def get_reverse_deps(self):\n        return []\n", encoding="utf-8"
    )
    files = ["app/page.tsx", "graph.py"]
    tsx = extract_target_sources(tmp_path, ["ConnectorCards"], files)
    py = extract_target_sources(tmp_path, ["DependencyGraph.get_reverse_deps"], files)
    assert "app/page.tsx :: ConnectorCards" in tsx and "return a;" in tsx
    assert "graph.py :: DependencyGraph" in py and "get_reverse_deps" in py


def test_class_method_target_emits_the_method_and_its_self_callees_only(tmp_path):
    from maf_test_harness.repo.analyzer import extract_target_sources

    (tmp_path / "big.py").write_text(
        "class Big:\n"
        "    def __init__(self):\n"
        "        self.rows = []\n"
        "\n"
        "    def helper(self, x):\n"
        "        return x * 2\n"
        "\n"
        "    def build(self):\n"
        "        return [self.helper(r) for r in self.rows]\n"
        "\n"
        "    def unrelated(self):\n"
        "        return 'never shown for Big.build'\n",
        encoding="utf-8",
    )
    text = extract_target_sources(tmp_path, ["Big.build"], ["big.py"])
    assert "def build(self):" in text and "def helper(self, x):" in text
    assert "unrelated" not in text and "__init__" not in text
    assert "class Big:" in text  # header kept so the block still parses as Python
    whole = extract_target_sources(tmp_path, ["Big"], ["big.py"])
    assert "def unrelated(self):" in whole  # a bare class name still means the whole class
