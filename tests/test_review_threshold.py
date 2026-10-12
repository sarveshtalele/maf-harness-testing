"""Review confidence gates: run override > HARNESS_{KIND}_REVIEW_MIN_CONFIDENCE > 95, parsed safely."""

import pytest

from maf_test_harness import settings


@pytest.fixture(autouse=True)
def no_env_overrides(monkeypatch):
    for kind in settings.REVIEW_KINDS:
        monkeypatch.delenv(f"HARNESS_{kind.upper()}_REVIEW_MIN_CONFIDENCE", raising=False)


def test_default_is_95_for_every_kind():
    assert [settings.review_threshold(k) for k in settings.REVIEW_KINDS] == [95, 95, 95]


def test_env_applies_per_kind_and_is_clamped(monkeypatch):
    monkeypatch.setenv("HARNESS_DESIGN_REVIEW_MIN_CONFIDENCE", "70")
    monkeypatch.setenv("HARNESS_TEST_REVIEW_MIN_CONFIDENCE", "10")  # below the floor: clamped to 50
    assert settings.review_threshold("design") == 70
    assert settings.review_threshold("test") == 50
    assert settings.review_threshold("plan") == 95


@pytest.mark.parametrize("bad", ["", "high", "80.5", "  ", "nan"])
def test_malformed_env_falls_back_to_the_default_instead_of_raising(monkeypatch, bad):
    monkeypatch.setenv("HARNESS_PLAN_REVIEW_MIN_CONFIDENCE", bad)
    assert settings.review_threshold("plan") == 95


def test_run_override_wins_over_env_and_default(monkeypatch):
    monkeypatch.setenv("HARNESS_TEST_REVIEW_MIN_CONFIDENCE", "80")
    state = {"confidence_thresholds": {"test": 88}}
    assert settings.review_threshold("test", state) == 88
    assert settings.review_threshold("test", {"confidence_thresholds": {"test": "junk"}}) == 80
    assert settings.review_threshold("test", {}) == 80


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError, match="Unknown review kind"):
        settings.review_threshold("generation")


def test_legacy_constant_is_an_alias_of_the_test_gate():
    assert settings.review_threshold("test") == settings.TEST_REVIEW_MIN_CONFIDENCE
