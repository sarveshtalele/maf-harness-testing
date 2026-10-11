import json

import pytest

from maf_test_harness.ui.errors import categorize, run_errors


@pytest.mark.parametrize(
    "text,cat",
    [
        ("Independent test review requires repair: x", "review_rejected"),
        ("cannot work: the apparent repository has no x", "import"),
        ("No module named 'foo'", "import"),
        ("source is invalid: '(' was never closed", "syntax"),
        ("EOF while parsing a string", "truncated"),
        ("needs exactly one concrete test declaration", "one_test_per_file"),
        ("generated file references unknown case IDs: ['A']", "unknown_case_ids"),
        ("Structured output failed schema validation", "schema"),
        ("[gateway] The model gateway was unreachable for several minutes (5 attempts).", "gateway"),
        ("MiddlewareFailure: service failed to complete the prompt: Connection error.", "gateway"),
        ("service failed to complete the prompt: Error code: 503 - upstream overloaded", "gateway"),
        ("service failed to complete the prompt: Error code: 429 - rate limited", "gateway"),
        ("boom", "other"),
        ("generated 503 cases for the module", "other"),
        # ordinary test output that mentions the same words must not be gateway failures
        ("assert resp.status_code == 503", "other"),
        ("Rate limit exceeded in test_rate_limit", "other"),
        ("'Connection error' in msg", "other"),
    ],
)
def test_categories(text, cat):
    assert categorize(text) == cat


def test_gateway_outage_has_a_remedy_and_import_errors_do_not():
    from maf_test_harness.qa.triage import remedy_for

    assert "unreachable or overloaded" in remedy_for("The model gateway was unreachable for about 5 minutes")
    assert "unreachable or overloaded" not in remedy_for("No module named 'foo'")
    for test_output in (
        "assert resp.status_code == 503",
        "Rate limit exceeded in test_rate_limit",
        "'Connection error' in msg",
    ):
        assert "unreachable or overloaded" not in remedy_for(test_output)


def write_audit(d, events):
    (d / "audit.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n")


def test_real_evidence_shape(tmp_path):
    long = "Independent test review requires repair: TC-1: " + "x" * 400
    write_audit(
        tmp_path,
        [
            {
                "event": "stage_error",
                "at": "2026-10-07T00:44:00",
                "stage": "test_planning",
                "error": "Invalid JSON: EOF while parsing a string",
            },
            {
                "event": "test_generation_batch_failed",
                "at": "2026-10-07T01:33:00",
                "batch": 2,
                "case_ids": ["TC-1"],
                "error": long,
            },
            {"event": "build_gate_failed_continuing", "at": "2026-10-07T02:00:00", "error": "tsc failed"},
        ],
    )
    view = {
        "error": "run broke",
        "pending": {"error": "Gate failed. More."},
        "state": {
            "artifacts": {
                "test_generation": {
                    "generation_failures": [{"batch": 2, "case_ids": ["TC-1"], "last_error": long}]
                },
                "test_execution": {"execution": {"quarantined": ["tests/a.test.ts"]}},
            }
        },
    }
    out = run_errors(tmp_path, view)
    cats = [i["category"] for i in out["items"]]
    assert cats.count("review_rejected") == 1  # generation_failures + audit event deduped
    assert out["counts"] == {
        "other": 2,
        "build_gate": 1,
        "review_rejected": 1,
        "truncated": 1,
        "quarantined": 1,
    }
    assert out["items"][0]["at"] == "2026-10-07T02:00:00"  # newest first; undated entries last
    first = next(i for i in out["items"] if i["category"] == "review_rejected")
    assert len(first["summary"]) <= 160 and first["detail"] == long and first["case_ids"] == ["TC-1"]
    assert sum(out["counts"].values()) == len(out["items"])


def test_missing_files_and_bad_view(tmp_path):
    assert run_errors(tmp_path / "nope", {}) == {"items": [], "counts": {}}
    assert run_errors(tmp_path, None) == {"items": [], "counts": {}}


def test_newest_first(tmp_path):
    write_audit(
        tmp_path,
        [
            {"event": "stage_error", "at": "2026-01-01", "stage": "a", "error": "old"},
            {"event": "stage_error", "at": "2026-02-01", "stage": "b", "error": "new"},
        ],
    )
    assert [i["summary"] for i in run_errors(tmp_path, {})["items"]] == ["new", "old"]
