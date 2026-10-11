import pytest

from maf_test_harness.qa.confidence import (
    RUBRIC,
    evidence_caps,
    mocks_target,
    model_score,
    resolve_target,
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
    assert target_symbol("UserList component") == "UserList"
    assert target_symbol("Component") == ""  # a bare capitalised word is not a confident symbol
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


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("GET /api/users/{id}", ("route", "")),
        ("CLI: foo --x", None),
        ("default export Foo (Foo.tsx)", None),
        ("User constructor", ("constructor", "User")),
        ("User.full_name (property)", ("symbol", "full_name")),
        ("Foo.bar (async)", ("symbol", "bar")),
        ("UserList component", ("symbol", "UserList")),
        ("useAuth hook", ("symbol", "useAuth")),
        ("Foo.bar(int)", ("symbol", "bar")),
        ("Login page", None),
        ("Checkout flow", None),
        ("main.list_users", ("symbol", "list_users")),
        ("User.__init__", ("constructor", "User")),
        ("Class#method", ("symbol", "method")),
        ("pkg.mod::func", ("symbol", "func")),
        ("src/api/users.ts getUser", ("symbol", "getUser")),
    ],
)
def test_only_confident_targets_resolve_to_a_symbol_or_route(target, expected):
    resolved = resolve_target(target)
    if expected is None:
        assert resolved is None
    else:
        assert (resolved.kind, resolved.symbol if resolved.kind != "route" else "") == expected


def test_route_targets_check_the_path_literal_with_wildcards():
    content = "def test_get():\n    assert client.get('/api/users/42').status_code == 200\n"
    assert caps_for(content, target="GET /api/users/{id}") == {}
    other = "def test_get():\n    assert client.get('/api/orders/1').status_code == 200\n"
    assert caps_for(other, target="GET /api/users/{id}") == {"target_not_invoked": 40}
    assert "target_source_missing" not in caps_for(content, target="GET /api/users/{id}", source_found=False)


@pytest.mark.parametrize(
    "target",
    ["CLI: foo --x", "default export Foo (Foo.tsx)", "Login page", "Checkout flow"],
)
def test_non_symbol_targets_skip_target_caps(target):
    unrelated = "def test_x():\n    assert 1 == 1\n"
    assert caps_for(unrelated, target=target, source_found=False) == {}


def test_a_symbol_mentioned_only_in_a_comment_or_string_is_not_invoked():
    commented = "# bar() is covered elsewhere\ndef test_x():\n    assert baz() == 1\n"
    quoted = "def test_x():\n    assert baz() == 'bar'\n"
    assert caps_for(commented, target="Foo.bar") == {"target_not_invoked": 40}
    assert caps_for(quoted, target="Foo.bar") == {"target_not_invoked": 40}


def test_assertions_inside_comments_do_not_count():
    assert caps_for("test('t', () => {\n  // expect(total).toBe(0);\n  total();\n});\n", target="total") == {
        "no_assertion": 30
    }


@pytest.mark.parametrize(
    "content",
    [
        "def test_x():\n    assert_equal(2, add(1, 1))\n",
        'func TestAdd(t *testing.T) {\n    if add(1, 1) != 2 {\n        t.Errorf("bad")\n    }\n}\n',
        "class T { @Test(expected = IllegalArgumentException.class) void t() { add(1, 1); } }\n",
        "const x = add(1, 1);\nrequire.Equal(t, 2, x)\n",
    ],
)
def test_additional_assertion_idioms_count(content):
    assert "no_assertion" not in caps_for(content, target="add")


def test_python_patch_of_another_module_with_the_same_name_is_not_a_self_mock():
    content = "from unittest.mock import patch\nfrom report import save\n\ndef test_save():\n    with patch('db.save'):\n        assert save() is True\n"
    assert caps_for(content, target="report.save") == {}
    assert not mocks_target("report.save", content)


def test_python_patch_of_the_units_own_module_is_still_a_self_mock():
    content = "from unittest.mock import patch\nfrom report import save\n\ndef test_save():\n    with patch('report.save'):\n        assert save() is True\n"
    assert caps_for(content, target="report.save") == {"target_mocked": 40}


def test_js_mock_of_another_module_is_not_a_self_mock():
    content = (
        "jest.mock('../../lib/api');\nimport { getUser } from '../api/users';\n"
        "test('x', () => { expect(getUser(1)).toBeDefined(); });\n"
    )
    assert caps_for(content, target="src/api/users.ts getUser") == {}


def test_js_mock_of_the_targets_own_module_is_a_self_mock():
    content = (
        "jest.mock('../api/users');\nimport { getUser } from '../api/users';\n"
        "test('x', () => { expect(getUser(1)).toBeDefined(); });\n"
    )
    assert caps_for(content, target="src/api/users.ts getUser") == {"target_mocked": 40}
