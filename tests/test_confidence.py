from maf_test_harness.qa.confidence import (
    RUBRIC,
    evidence_caps,
    mocks_target,
    model_score,
    review_accepted,
    score_review,
    target_symbol,
    verification_note,
    weakest_dimensions,
)

FULL = dict(RUBRIC)


def caps_for(content, target="service.add", verdict="supported", source_found=True):
    return {
        cap["rule"]: cap["cap"]
        for cap in evidence_caps(target=target, content=content, verdict=verdict, source_found=source_found)
    }


PY_OK = "from service import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
TS_OK = "import { Cart } from './cart';\ntest('total', () => {\n  expect(new Cart().total()).toBe(0);\n});\n"
JAVA_OK = "class ItemTest {\n  @Test void setsPrice() {\n    Item item = new Item();\n    item.setPrice(2);\n    assertEquals(2, item.getPrice());\n  }\n}\n"


def test_target_symbol_takes_the_last_identifier():
    assert target_symbol("Class.method") == "method"
    assert target_symbol("service.add") == "add"
    assert target_symbol("Component") == "Component"
    assert target_symbol("") == ""


def test_supported_samples_have_no_caps_in_python_typescript_and_java():
    assert caps_for(PY_OK) == {}
    assert caps_for(TS_OK, target="Cart.total") == {}
    assert caps_for(JAVA_OK, target="Item.setPrice") == {}


def test_target_not_invoked_caps_at_40():
    assert caps_for("def test_x():\n    assert 1 == 1\n") == {"target_not_invoked": 40}
    assert caps_for("def test_x():\n    assert helper() == 1\n") == {"target_not_invoked": 40}


def test_missing_assertion_caps_at_30_in_each_language():
    assert caps_for("from service import add\n\ndef test_add():\n    add(1, 2)\n") == {"no_assertion": 30}
    assert caps_for("test('t', () => { new Cart().total(); });", target="Cart.total") == {"no_assertion": 30}
    assert caps_for("class T { void t() { item.setPrice(2); } }", target="Item.setPrice") == {
        "no_assertion": 30
    }


def test_missing_target_source_caps_at_50_and_forces_insufficient_evidence():
    assert caps_for(PY_OK, source_found=False) == {"target_source_missing": 50}
    review = {
        "case_id": "TC-1",
        "scores": FULL,
        "confidence": 95,
        "verdict": "supported",
        "reason": "r",
        "source_files": ["service.py"],
    }
    scored = score_review(review, target="service.add", content=PY_OK, source_found=False)
    assert scored["verdict"] == "insufficient_evidence" and scored["confidence"] == 50


def test_undeclared_target_skips_target_rules():
    assert caps_for("def test_v():\n    assert value() == 2\n", target="") == {}


def test_python_patch_of_the_target_caps_at_40_but_not_of_other_names():
    mocked = "from unittest.mock import patch\nfrom service import add\n\ndef test_add():\n    with patch('service.add'):\n        assert add(1, 2) == 3\n"
    other = "from unittest.mock import patch\nfrom service import add\n\ndef test_add():\n    with patch('service.helper'):\n        assert add(1, 2) == 3\n"
    assert caps_for(mocked) == {"target_mocked": 40}
    assert caps_for(other) == {}
    assert mocks_target("Service.add", "patch.object(Service, 'add')")


def test_typescript_spy_and_module_mock_of_the_target_are_flagged():
    spied = "import { Cart } from './cart';\njest.spyOn(cart, 'total').mockReturnValue(0);\nexpect(cart.total()).toBe(0);\n"
    module_mocked = "jest.mock('./cart');\nexpect(new Cart().total()).toBe(0);\n"
    assert caps_for(spied, target="Cart.total") == {"target_mocked": 40}
    assert caps_for(module_mocked, target="Cart.total") == {"target_mocked": 40}
    assert not mocks_target("Cart.total", "jest.mock('./invoice');\nexpect(new Cart().total()).toBe(0);")


def test_java_mock_of_the_owning_class_is_flagged_but_a_real_instance_is_not():
    mocked = "class ItemTest {\n  @Mock Item item;\n  @Test void t() { item.setPrice(2); assertEquals(2, item.getPrice()); }\n}\n"
    real = "class ItemTest {\n  @Test void t() { Item item = new Item(); item.setPrice(2); assertEquals(2, item.getPrice()); }\n}\n"
    assert caps_for(mocked, target="Item.setPrice") == {"target_mocked": 40}
    assert caps_for(real, target="Item.setPrice") == {}
    assert mocks_target("Item.setPrice", "Mockito.mock(Item.class)")


def test_verdict_caps_follow_the_reviewer_verdict():
    assert caps_for(PY_OK, verdict="needs_revision") == {"verdict_needs_revision": 49}
    assert caps_for(PY_OK, verdict="insufficient_evidence") == {"verdict_insufficient_evidence": 59}


def test_host_confidence_is_the_rubric_sum_capped_by_the_lowest_applicable_cap():
    review = {"case_id": "TC-1", "scores": FULL, "confidence": 95, "verdict": "supported", "reason": "r"}
    assert model_score(FULL) == 100
    scored = score_review(
        review, target="service.add", content="def test_x():\n    assert 1 == 1\n", source_found=True
    )
    assert scored["confidence"] == 40 and scored["model_confidence"] == 95
    assert scored["model_score"] == 100 and scored["caps"][0]["rule"] == "target_not_invoked"
    clean = score_review(review, target="service.add", content=PY_OK, source_found=True)
    assert clean["confidence"] == 100 and clean["caps"] == []


def test_review_acceptance_needs_supported_verdict_and_threshold():
    assert review_accepted({"verdict": "supported", "confidence": 85}, 85)
    assert not review_accepted({"verdict": "supported", "confidence": 84}, 85)
    assert not review_accepted({"verdict": "needs_revision", "confidence": 99}, 85)
    assert not review_accepted(None, 85)


def test_weakest_dimensions_are_listed_by_share_of_their_maximum():
    scores = {
        "source_and_api_match": 20,
        "assertion_checks_real_behavior": 30,
        "fixture_validity": 15,
        "isolation_and_determinism": 10,
        "traceability": 10,
    }
    assert weakest_dimensions(scores) == [
        "isolation_and_determinism 10/15",
        "fixture_validity 15/20",
        "source_and_api_match 20/25",
    ]


def test_verification_note_names_the_dimensions_caps_and_reviewer_reason():
    review = {
        "confidence": 40,
        "verdict": "supported",
        "reason": "Never calls add.",
        "scores": FULL,
        "caps": [{"rule": "target_not_invoked", "cap": 40, "reason": "no add"}],
    }
    note = verification_note(review, 85)
    assert "confidence 40 (threshold 85, verdict supported)" in note
    assert "caps: target_not_invoked cap 40: no add" in note
    assert "reviewer: Never calls add." in note
