import pytest

from maf_test_harness.agent.runtime import AgentOutputError, extract_json_object


def test_extract_json_object_accepts_plain_json() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_accepts_fenced_json() -> None:
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_object_rejects_non_json() -> None:
    with pytest.raises(AgentOutputError):
        extract_json_object("no structured object here")


def test_extract_json_object_accepts_python_style_mapping() -> None:
    assert extract_json_object("Result: {'a': 1, 'ok': True}") == {"a": 1, "ok": True}
