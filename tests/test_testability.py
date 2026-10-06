from maf_test_harness.llm.prompts import generation_prompt
from maf_test_harness.repo import testability as tb
from maf_test_harness.repo.testability import analyze_testability, retarget_cases

CONTROLLER = """package app;
import javafx.fxml.FXML;
import javafx.scene.control.TableColumn;
public class AddPurchaseController {
    @FXML private TableView table;
    private Connection con;
    public AddPurchaseController() {
        DbConnection dbc = DbConnection.getDatabaseConnection();
        con = dbc.getConnection();
    }
    public void initialize() {
        TableColumn<Item, String> col = new TableColumn<>("x");
        table.getColumns().add(col);
    }
    public void setPrice(double p) {
        this.price = p;
        calculatePrice();
    }
    public void calculatePrice() {
        amount = qty * price;
        total.setText(String.valueOf(amount));
    }
}
"""
PURE = "public class Calc {\n    public int add(int a, int b) {\n        return a + b;\n    }\n}\n"
PY = "import sqlite3\nclass Repo:\n    def __init__(self):\n        self.db = sqlite3.connect('x.db')\n    def get(self):\n        return 1\n"


def _repo(tmp_path, files, pom=""):
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    if pom:
        (tmp_path / "pom.xml").write_text(pom, encoding="utf-8")
    return analyze_testability(tmp_path, list(files))["classes"]


def test_static_constructor_and_javafx(tmp_path):
    info = _repo(tmp_path, {"A.java": CONTROLLER})["A.java::AddPurchaseController"]
    assert info["level"] == "low"
    text = " ".join(info["reasons"])
    assert "getDatabaseConnection" in text and "toolkit" in text
    assert "objects created locally in initialize are not injectable" in text
    assert "integration_test" in info["allowed_strategies"]
    assert not any("mockStatic" in s for s in info["allowed_strategies"])


def test_pure_and_python(tmp_path):
    classes = _repo(tmp_path, {"Calc.java": PURE, "r.py": PY})
    assert classes["Calc.java::Calc"]["level"] == "high"
    assert classes["r.py::Repo"]["level"] == "low"


def test_mockito_inline_adds_mock_static(tmp_path):
    pom = "<dependency><artifactId>mockito-inline</artifactId></dependency>"
    info = _repo(tmp_path, {"A.java": CONTROLLER}, pom)["A.java::AddPurchaseController"]
    assert any("mockStatic" in s for s in info["allowed_strategies"])


def test_retarget_set_price_to_calculate_price(tmp_path):
    (tmp_path / "A.java").write_text(CONTROLLER, encoding="utf-8")
    case = {
        "id": "T1",
        "title": "setPrice calculates amount from qty*price",
        "expected_result": "amount equals qty * price",
        "target": "A.java AddPurchaseController.setPrice",
    }
    notes = retarget_cases([case], tmp_path, ["A.java"])
    assert case["target"].endswith("AddPurchaseController.calculatePrice") and "callee" in notes[0]
    untouched = {**case, "target": "A.java AddPurchaseController.calculatePrice"}
    assert retarget_cases([untouched], tmp_path, ["A.java"]) == []


def test_prompt_section_only_batch_relevant(tmp_path):
    classes = _repo(tmp_path, {"A.java": CONTROLLER, "Calc.java": PURE})
    section = tb.testability_for_targets({"classes": classes}, ["Calc.java Calc.add"])
    assert list(section) == ["Calc.java::Calc"]
    prompt = generation_prompt({"assignment": "x", "target_source": "s", "testability": section})
    assert "AddPurchaseController" not in prompt and prompt.index("target_source") < prompt.index(
        "testability"
    )
