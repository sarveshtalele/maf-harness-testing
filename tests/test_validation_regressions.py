import pytest

from maf_test_harness.skill_contracts import validate_generated_files


def payload(code, path="test_example.py"):
    return {"files": [{"path": path, "content": "# CASE_ID: TC-1\n" + code, "case_ids": ["TC-1"]}]}


@pytest.mark.parametrize(
    "code",
    [
        "def helper():\n    def test_nested():\n        assert 1 == 2\n",
        'text = """\ndef test_fake():\n    assert 1 == 2\n"""\n',
        'def test_empty():\n    """Documented but empty."""\n    pass\n' + "# padding\n" * 20,
        "def test_fake():\n    assert True\n" + "# padding\n" * 20,
        "class Helper:\n    def test_not_collected(self):\n        assert self.value == 3\n",
    ],
)
def test_rejects_noncollectable_or_empty_python_tests(code):
    with pytest.raises(ValueError):
        validate_generated_files(payload(code), {"TC-1"})


def test_duplicate_case_mapping_rejected():
    document = payload("def test_one():\n    assert value() == 1\n")
    document["files"].append({**document["files"][0], "path": "test_second.py"})
    with pytest.raises(ValueError, match="more than once"):
        validate_generated_files(document, {"TC-1"})


def test_constant_assertion_cannot_claim_application_coverage():
    with pytest.raises(ValueError, match="constant"):
        validate_generated_files(payload("def test_fake():\n    assert 1 + 1 == 2\n"), {"TC-1"})
