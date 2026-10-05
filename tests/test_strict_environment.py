"""STRICT GUARDRAIL: no stage after environment_setup starts until every declared requirement is installed and consistent.

Found on a live run on personal-finance-using-RAG: `chromadb==0.4.24` needs `chroma-hnswlib==0.7.3`, which has no wheel for
Python 3.12, so the install failed; the harness warned, the reviewer approved, and 4 of the 11 failing tests then failed only
because the package was missing. The environment gate now blocks instead of warning, and proposes another interpreter.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
from test_execution_stage import local_pytest_plan

import maf_test_harness.workflow as wf
from maf_test_harness.envs import env_check, recovery, spec, toolchains
from maf_test_harness.ui.runtime_service import RuntimeService
from maf_test_harness.workflow_steps import execution as execution_steps

CONFLICT = {
    "package": "fastapi",
    "version": "0.110.0",
    "requirement": "starlette<0.37.0,>=0.36.3",
    "dependency": "starlette",
    "installed": "1.6.0",
}
COMPILER_STEP = {
    "name": "install-requirements",
    "status": "failed",
    "diagnoses": [{"code": "ENV-PY-001", "category": "environment"}],
}


class Harnessed:
    """A workflow host with a controllable environment install."""

    def __init__(self, tmp_path, monkeypatch):
        self.calls = {"provision": [], "plans": [], "wipe": []}
        self.report = {"status": "ready", "steps": [], "warnings": []}
        self.missing: list[str] = []

        def plan(stack, root, cache_dir, timeout=900, needs=None, python_override=None):
            self.calls["plans"].append(python_override)
            base = local_env_plan(stack, root, cache_dir, timeout, needs)
            if python_override:
                base["toolchains"] = [
                    {"kind": "python", "version": python_override, "status": "download", "exact": True}
                ]
            return base

        def execute(steps, workspace, out_dir, **kwargs):
            out_dir.mkdir(parents=True)
            (out_dir / "report.json").write_text("{}")
            return json.loads(json.dumps(self.report))

        monkeypatch.setattr(wf, "clone_repository", clone_fixture)
        monkeypatch.setattr(execution_steps, "build_plan", local_pytest_plan)
        monkeypatch.setattr(execution_steps, "build_environment_plan", plan)
        monkeypatch.setattr(execution_steps, "execute_setup", execute)
        monkeypatch.setattr(execution_steps, "provision_for", lambda *a, **k: self._provision(k))
        monkeypatch.setattr(execution_steps.env_check, "missing_packages", lambda ws: list(self.missing))
        monkeypatch.setattr(
            wf.env_recovery, "wipe_venv", lambda ws: self.calls["wipe"].append(str(ws)) or True
        )
        self.service = RuntimeService()
        self.host = wf.Harness(
            tmp_path / "run",
            skills_root=Path("skills"),
            requirements_mode="model",
            runtime=ScriptedStageRuntime(),
            cache_dir=tmp_path / "cache",
        )
        self.service.submit(self.host.start("https://github.com/acme/demo")).result(15)

    def _provision(self, kwargs):
        self.calls["provision"].append(kwargs.get("python_override"))
        from maf_test_harness.envs.toolchains import Provisioned

        return Provisioned()

    def answer(self, **response):
        return self.service.submit(self.host.respond(self.host.view["pending"]["id"], response)).result(120)

    def to_env_gate(self):
        for _ in range(6):  # clone, repo, requirements
            self.answer(decision="approve")
        assert (
            self.host.view["pending"]["stage"] == "environment_setup"
            and self.host.view["pending"]["kind"] == "tool"
        )

    @property
    def pending(self):
        return self.host.view["pending"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    harness = Harnessed(tmp_path, monkeypatch)
    yield harness
    harness.service.close()


def test_a_missing_declared_package_blocks_the_next_stage_instead_of_warning(env):
    env.missing = ["chromadb"]
    env.to_env_gate()
    env.answer(decision="approve")  # approve the install
    assert env.pending["kind"] == "error" and env.pending["stage"] == "environment_setup"
    assert "strict guardrail" in env.pending["title"]
    assert "chromadb" in env.pending["error"] and "no later stage will start" in env.pending["error"]
    assert env.host.state["environment"]["status"] == "incomplete"
    assert "artifact" not in env.pending["details"]  # there is no review to approve


def test_a_failed_install_step_blocks(env):
    env.report = {
        "status": "setup_failed",
        "error": "Step 'install' failed: failed",
        "steps": [],
        "warnings": [],
    }
    env.to_env_gate()
    env.answer(decision="approve")
    assert env.pending["kind"] == "error" and "Step 'install' failed" in env.pending["error"]


def test_unresolved_version_conflicts_block(env):
    env.report = {
        "status": "ready",
        "steps": [],
        "warnings": [],
        "dependency_conflicts": {"found": [CONFLICT], "repaired": [], "remaining": [CONFLICT]},
    }
    env.to_env_gate()
    env.answer(decision="approve")
    assert env.pending["kind"] == "error"
    assert "fastapi needs starlette<0.37.0,>=0.36.3, has 1.6.0" in env.pending["error"]


def test_a_repaired_conflict_does_not_block(env):
    env.report = {
        "status": "ready",
        "steps": [],
        "warnings": ["pip reported version conflicts (starlette); repaired"],
        "dependency_conflicts": {"found": [CONFLICT], "repaired": [{"round": 1}], "remaining": []},
    }
    env.to_env_gate()
    env.answer(decision="approve")
    assert env.pending["kind"] == "review" and env.pending["stage"] == "environment_setup"


def test_a_complete_environment_proceeds_to_the_review_and_the_next_stage(env):
    env.to_env_gate()
    env.answer(decision="approve")
    assert env.pending["kind"] == "review"
    env.answer(decision="approve")
    assert env.pending["stage"] == "test_execution"
    assert env.host.state["environment"]["status"] == "ready"


def test_the_stage_guard_refuses_to_start_any_later_stage_while_incomplete(env):
    env.missing = ["chromadb"]
    env.to_env_gate()
    env.answer(decision="approve")
    for stage in ("test_planning", "test_design", "test_generation", "test_execution"):
        with pytest.raises(ValueError, match="Strict environment guardrail"):
            env.service.submit(env.host.enter(stage, env.host.state, None)).result(10)


def test_the_gate_offers_only_retry_or_stop(env):
    env.missing = ["chromadb"]
    env.to_env_gate()
    env.answer(decision="approve")
    env.answer(
        decision="approve"
    )  # approving is not an option at this gate: it is asked again, nothing advances
    assert env.pending["kind"] == "error" and env.pending["stage"] == "environment_setup"
    assert "Choose retry or stop" in env.pending["error"]
    assert env.host.state["environment"]["status"] == "incomplete"


def test_retry_after_the_requirement_was_installed_lets_the_run_proceed(env):
    env.missing = ["chromadb"]
    env.to_env_gate()
    env.answer(decision="approve")
    assert env.pending["kind"] == "error"
    env.missing = []  # the human installed the build tools / the package
    env.answer(decision="retry")  # re-plan
    assert env.pending["kind"] == "tool"
    env.answer(decision="approve")
    assert env.pending["kind"] == "review" and env.host.state["environment"]["status"] == "ready"


# --- the interpreter fallback ------------------------------------------------------------------------------------


def test_a_compile_failure_proposes_another_python_and_the_retry_asks_for_the_download(env, monkeypatch):
    env.missing = ["chromadb"]
    env.report = {"status": "ready", "steps": [COMPILER_STEP], "warnings": []}
    monkeypatch.setattr(wf.env_recovery, "python_fallback", lambda report, ws, tried=(), **k: "3.11")
    env.to_env_gate()
    env.answer(decision="approve")
    assert env.pending["details"]["python_fallback"] == "3.11"
    assert "Python 3.11" in env.pending["error"] and "you approve it" in env.pending["error"]
    assert env.calls["provision"] == [None]  # nothing was downloaded for the fallback yet

    env.answer(decision="retry")
    assert env.calls["wipe"], "the venv built with the old interpreter is removed"
    assert env.host.state["env_python"] == "3.11" and env.host.state["env_python_tried"] == ["3.11"]
    assert env.pending["kind"] == "tool" and env.calls["plans"][-1] == "3.11"
    assert env.pending["details"]["toolchains"][0]["version"] == "3.11"  # the approval lists the download

    env.missing = []  # the rebuilt environment has everything
    env.answer(decision="approve")
    assert env.calls["provision"][-1] == "3.11"
    assert env.pending["kind"] == "review" and env.host.state["environment"]["status"] == "ready"


def test_without_a_fallback_the_gate_says_what_to_install(env, monkeypatch):
    env.missing = ["chromadb"]
    env.report = {"status": "ready", "steps": [COMPILER_STEP], "warnings": []}
    monkeypatch.setattr(wf.env_recovery, "python_fallback", lambda *a, **k: None)
    env.to_env_gate()
    env.answer(decision="approve")
    assert env.pending["details"]["python_fallback"] is None
    assert "Microsoft C++ Build Tools" in env.pending["error"]
    env.answer(decision="retry")
    assert not env.calls["wipe"] and "env_python" not in env.host.state  # nothing to switch to


def test_strict_mode_can_be_switched_off_for_the_old_behaviour(env, monkeypatch):
    monkeypatch.setattr(execution_steps, "STRICT_ENV", False)
    env.missing = ["chromadb"]
    env.to_env_gate()
    env.answer(decision="approve")
    assert env.pending["kind"] == "review"
    assert any("NOT installed" in w for w in env.pending["details"]["artifact"]["report"]["warnings"])


def test_an_explicit_skip_is_still_the_reviewers_choice(env):
    env.to_env_gate()
    env.answer(decision="skip")
    assert env.pending["kind"] == "review" and env.pending["details"]["artifact"]["status"] == "skipped"


# --- the pieces --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("codes", "current", "tried", "expected"),
    [
        (["ENV-PY-001"], "3.12", [], "3.11"),
        (["ENV-PY-002"], "3.12", ["3.11"], "3.10"),
        (["ENV-PY-003"], "3.11", [], "3.10"),
        (["ENV-PY-001"], "3.13", ["3.11", "3.10"], "3.12"),
        (["ENV-PY-001"], "3.12", ["3.11", "3.10"], None),  # everything tried
        (["ENV-COMMON-001"], "3.12", [], None),  # a timeout is not fixed by another Python
        ([], "3.12", [], None),
    ],
)
def test_python_fallback_choice(tmp_path, codes, current, tried, expected):
    report = {"steps": [{"diagnoses": [{"code": c} for c in codes]}]}
    assert (
        recovery.python_fallback(report, tmp_path, tried, current=current, available=lambda v: True)
        == expected
    )


def test_python_fallback_needs_an_installed_or_installable_interpreter(tmp_path, monkeypatch):
    report = {"steps": [{"diagnoses": [{"code": "ENV-PY-001"}]}]}
    monkeypatch.setattr(recovery.shutil, "which", lambda name: None)  # no uv
    monkeypatch.setattr(toolchains, "find", lambda kind, want, root=None, exact=False: None)
    assert recovery.python_fallback(report, tmp_path, current="3.12") is None
    monkeypatch.setattr(
        toolchains, "find", lambda kind, want, root=None, exact=False: object() if want == "3.10" else None
    )
    assert recovery.python_fallback(report, tmp_path, current="3.12") == "3.10"  # 3.11 is not there, 3.10 is
    monkeypatch.setattr(recovery.shutil, "which", lambda name: "uv")
    monkeypatch.setattr(toolchains, "find", lambda *a, **k: None)
    assert recovery.python_fallback(report, tmp_path, current="3.12") == "3.11"  # uv can install it


def test_the_venv_python_version_and_the_venv_wipe_are_scoped_to_the_harness_venv(tmp_path):
    venv = tmp_path / ".harness-venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = x\nversion = 3.12.13\n")
    (tmp_path / "keep.txt").write_text("x")
    assert env_check.venv_python_version(tmp_path) == "3.12"
    assert recovery.wipe_venv(tmp_path) and not venv.exists() and (tmp_path / "keep.txt").exists()
    assert env_check.venv_python_version(tmp_path) is None and recovery.wipe_venv(tmp_path) is False


def test_an_exact_requirement_means_that_minor_and_replaces_the_projects_requirement(tmp_path, monkeypatch):
    assert toolchains.satisfies("python", "3.12.1", "3.11") is True  # "at least"
    assert toolchains.satisfies("python", "3.12.1", "3.11", exact=True) is False
    assert toolchains.satisfies("python", "3.11.9", "3.11", exact=True) is True
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.9"\n')
    assert spec.detect_spec(tmp_path, "python").requirements[0].version == "3.9"
    override = spec.detect_spec(tmp_path, "python", "3.11").requirements
    assert [(r.version, r.exact) for r in override] == [("3.11", True)]


def test_the_override_reaches_the_toolchain_plan_and_provisioning(tmp_path, monkeypatch):
    from maf_test_harness import envs

    seen = []
    monkeypatch.setattr(
        toolchains, "find", lambda kind, want, root=None, exact=False: seen.append((want, exact))
    )
    rows = envs.plan_toolchains(tmp_path, "python", python_override="3.11")
    assert rows[0]["version"] == "3.11" and rows[0]["status"] == "download" and seen == [("3.11", True)]
    captured = {}
    monkeypatch.setattr(toolchains, "ensure", lambda reqs, **k: captured.update(reqs=reqs) or "done")
    assert envs.provision_for(tmp_path, "python", allow_download=True, python_override="3.11") == "done"
    assert captured["reqs"][0].exact and captured["reqs"][0].version == "3.11"
    assert sys.version_info >= (3, 11)
