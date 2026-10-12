"""Review confidence gates are hard gates (95-100): run override > HARNESS_{KIND}_REVIEW_MIN_CONFIDENCE > 95."""

import json

import pytest
from test_ui_api import local_api, wait  # noqa: F401

from maf_test_harness import settings


@pytest.fixture(autouse=True)
def no_env_overrides(monkeypatch):
    for kind in settings.REVIEW_KINDS:
        monkeypatch.delenv(f"HARNESS_{kind.upper()}_REVIEW_MIN_CONFIDENCE", raising=False)


def test_default_is_95_for_every_kind():
    assert [settings.review_threshold(k) for k in settings.REVIEW_KINDS] == [95, 95, 95]


def test_env_applies_per_kind_inside_the_hard_range(monkeypatch):
    monkeypatch.setenv("HARNESS_DESIGN_REVIEW_MIN_CONFIDENCE", "97")
    monkeypatch.setenv("HARNESS_TEST_REVIEW_MIN_CONFIDENCE", "100")
    assert settings.review_threshold("design") == 97
    assert settings.review_threshold("test") == 100
    assert settings.review_threshold("plan") == 95


def test_values_outside_the_hard_range_are_clamped_never_weakened(monkeypatch):
    monkeypatch.setenv("HARNESS_TEST_REVIEW_MIN_CONFIDENCE", "10")  # below the floor: raised to 95
    monkeypatch.setenv("HARNESS_PLAN_REVIEW_MIN_CONFIDENCE", "150")  # above the ceiling: lowered to 100
    assert settings.review_threshold("test") == 95
    assert settings.review_threshold("plan") == 100
    assert settings.review_threshold("design", {"confidence_thresholds": {"design": 70}}) == 95


@pytest.mark.parametrize("bad", ["", "high", "80.5", "  ", "nan", "true"])
def test_malformed_env_falls_back_to_the_default_instead_of_raising(monkeypatch, bad):
    monkeypatch.setenv("HARNESS_PLAN_REVIEW_MIN_CONFIDENCE", bad)
    assert settings.review_threshold("plan") == 95


@pytest.mark.parametrize("bad", [None, True, "junk", 96.5, ["97"], {"x": 1}])
def test_malformed_run_override_falls_back_to_the_env_then_the_default(monkeypatch, bad):
    monkeypatch.setenv("HARNESS_TEST_REVIEW_MIN_CONFIDENCE", "96")
    assert settings.review_threshold("test", {"confidence_thresholds": {"test": bad}}) == 96
    monkeypatch.delenv("HARNESS_TEST_REVIEW_MIN_CONFIDENCE")
    assert settings.review_threshold("test", {"confidence_thresholds": {"test": bad}}) == 95


def test_run_override_wins_over_env_and_default(monkeypatch):
    monkeypatch.setenv("HARNESS_TEST_REVIEW_MIN_CONFIDENCE", "96")
    assert settings.review_threshold("test", {"confidence_thresholds": {"test": 98}}) == 98
    assert settings.review_threshold("test", {}) == 96
    assert settings.review_threshold("test", {"confidence_thresholds": "junk"}) == 96


def test_the_test_gate_helper_reads_the_run_state():
    assert settings.test_review_threshold({"confidence_thresholds": {"test": 99}}) == 99
    assert settings.test_review_threshold(None) == 95


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError, match="Unknown review kind"):
        settings.review_threshold("generation")


def test_legacy_constant_is_an_alias_of_the_test_gate():
    assert settings.review_threshold("test") == settings.TEST_REVIEW_MIN_CONFIDENCE


@pytest.mark.parametrize(
    "thresholds",
    [{"design": 70}, {"design": 101}, {"design": True}, {"design": "97"}, {"generation": 97}, ["design"]],
)
def test_the_api_refuses_a_gate_outside_the_hard_range(local_api, thresholds):  # noqa: F811
    with pytest.raises(ValueError, match="confidence_thresholds|hard gates"):
        local_api.start({"repo_url": "https://github.com/acme/demo", "confidence_thresholds": thresholds})


def test_a_per_run_gate_is_stored_in_the_run_state_and_listed_for_the_ui(local_api):  # noqa: F811
    api = local_api
    key = api.start({"repo_url": "https://github.com/acme/demo", "confidence_thresholds": {"design": 99}})[
        "id"
    ]
    wait(api)
    run = json.loads((api.run_path(key) / "run.json").read_text(encoding="utf-8"))
    assert run["state"]["confidence_thresholds"] == {"design": 99}
    assert api.read(key)["state"]["confidence_thresholds"] == {"design": 99}
