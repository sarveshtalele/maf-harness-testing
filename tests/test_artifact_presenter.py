from maf_test_harness.artifact_presenter import humanize, json_to_markdown


def test_humanize():
    assert humanize("test_generation") == "Test Generation"


def test_json_to_markdown_renders_scalar_lists_and_tables():
    payload = {
        "summary": "Generated tests",
        "frameworks": ["pytest", "Playwright"],
        "cases": [
            {"id": "TC-POS-LOGIN-1234", "title": "Login works", "priority": "P1"},
            {"id": "TC-NEG-LOGIN-5678", "title": "Invalid login rejected", "priority": "P1"},
        ],
    }
    output = json_to_markdown(payload, title="Test Design")
    assert "# Test Design" in output
    assert "**Summary:** Generated tests" in output
    assert "- pytest" in output
    assert "| Id | Title | Priority |" in output
    assert "TC-POS-LOGIN-1234" in output


def test_json_to_markdown_renders_generated_source_as_code():
    payload = {
        "files": [
            {
                "path": "tests/test_sample.py",
                "case_ids": ["TC-POS-SAMPLE-1234"],
                "content": "def test_sample():\n    assert 1 == 1\n",
            }
        ]
    }
    output = json_to_markdown(payload, title="Generated Tests")
    assert "```python" in output
    assert "def test_sample():" in output
