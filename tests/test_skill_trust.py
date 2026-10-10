import base64
import io
import stat
import zipfile

import pytest

from maf_test_harness.agent.runtime import MAFHarnessRuntime
from maf_test_harness.skills import trust
from maf_test_harness.skills.config import ROOT, skills_for
from maf_test_harness.skills.registry import discover_skills
from maf_test_harness.skills.scripts import HarnessScriptRunner, ScriptRefused
from maf_test_harness.ui.api import LocalAPI

REPO = ROOT / "skills"
MANIFEST = (
    "name: go-helper\nstages: [test_generation]\nrole: companion\nkind: model\npurpose: follow Go idioms\n"
    "applies_when:\n  languages: [Go]\n"
)
FILES = {
    "SKILL.md": "---\nname: go-helper\ndescription: d\n---\n# Go\n",
    "plugin.yaml": MANIFEST,
    "scripts/hello.py": "print('hi')\n",
}


@pytest.fixture(autouse=True)
def stores(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("HARNESS_PLUGIN_APPROVALS", str(tmp_path / "approvals.json"))
    return tmp_path


def make_zip(files=FILES, prefix="", extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in files.items():
            z.writestr(prefix + name, body)
        for info, body in extra or []:
            z.writestr(info, body)
    return base64.b64encode(buf.getvalue()).decode()


def state(languages):
    return {"artifacts": {"repo_analysis": {"languages": dict.fromkeys(languages, 1)}}}


def runner():
    return HarnessScriptRunner(REPO, tiers=("model",), plugin_roots=(trust.plugin_dir(),))


def test_import_is_unverified_and_hidden_until_approved():
    result = trust.import_skill(
        REPO, zip_base64=make_zip(prefix="go-helper/")
    )  # single top-level folder is fine
    assert result["name"] == "go-helper" and result["source"] == "plugin"
    assert not result["verified"] and not result["approved"]
    assert any(p.name == "go-helper" for p in discover_skills([REPO, trust.plugin_dir()]))
    assert "go-helper" not in skills_for("test_generation", state(["Go"])).companions
    trust.approve(REPO, "go-helper")
    assert "go-helper" in skills_for("test_generation", state(["Go"])).companions
    assert "go-helper" not in skills_for("test_generation", state(["Python"])).companions


def test_scripts_blocked_until_approved_and_revoked_by_any_change():
    trust.import_skill(REPO, zip_base64=make_zip())
    with pytest.raises(ScriptRefused):
        runner().check("go-helper", "scripts/hello.py")
    trust.approve(REPO, "go-helper")
    live = runner()
    assert live.check("go-helper", "scripts/hello.py").is_file()
    assert live.run("go-helper", "scripts/hello.py")["stdout"].strip() == "hi"
    (trust.plugin_dir() / "go-helper" / "SKILL.md").write_text("---\nname: go-helper\ndescription: x\n---\n")
    with pytest.raises(ScriptRefused, match="changed since it was approved"):
        live.check("go-helper", "scripts/hello.py")  # same runner, files changed after construction
    with pytest.raises(ScriptRefused):
        runner().check("go-helper", "scripts/hello.py")
    assert "go-helper" not in skills_for("test_generation", state(["Go"])).companions


def test_unlocked_repo_skill_needs_approval_but_locked_ones_do_not(tmp_path):
    assert all(trust.trust(REPO, p.path)["verified"] for p in discover_skills([REPO]))
    with pytest.raises(ValueError, match="no approval needed"):
        trust.approve(REPO, "test-planning")
    repo = tmp_path / "skills"
    (repo / "mine").mkdir(parents=True)
    (repo / "mine" / "SKILL.md").write_text("---\nname: mine\ndescription: d\n---\n")
    assert trust.trust(repo, repo / "mine") == {
        "source": "repo",
        "verified": False,
        "approved": False,
        "trusted": False,
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"../evil.txt": "x"},
        {"/abs.txt": "x"},
        {"a/../../evil.txt": "x"},
        {"C:/evil.txt": "x"},
    ],
)
def test_zip_path_traversal_rejected(bad):
    with pytest.raises(ValueError, match="Unsafe path"):
        trust.import_skill(REPO, zip_base64=make_zip({**FILES, **bad}))
    assert not (trust.plugin_dir() / "go-helper").exists()


def test_zip_symlink_oversize_and_bad_manifest_rejected(monkeypatch):
    link = zipfile.ZipInfo("scripts/link.py")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError, match="Symbolic links"):
        trust.import_skill(REPO, zip_base64=make_zip(extra=[(link, "/etc/passwd")]))
    monkeypatch.setattr(trust, "MAX_IMPORT_BYTES", 100)
    with pytest.raises(ValueError, match="too large"):
        trust.import_skill(REPO, zip_base64=make_zip({**FILES, "big.md": "x" * 500}))
    monkeypatch.undo()
    with pytest.raises(ValueError, match="host|entrypoint"):
        trust.import_skill(REPO, zip_base64=make_zip({**FILES, "plugin.yaml": MANIFEST + "kind: host\n"}))
    with pytest.raises(ValueError):
        trust.import_skill(REPO, zip_base64=make_zip({**FILES, "plugin.yaml": "name: ../x\n"}))
    with pytest.raises(ValueError, match="already exists"):
        trust.import_skill(
            REPO,
            zip_base64=make_zip(
                {
                    "SKILL.md": FILES["SKILL.md"].replace("go-helper", "test-planning"),
                    "plugin.yaml": MANIFEST.replace("go-helper", "test-planning"),
                }
            ),
        )


def test_folder_import_rejects_symlinks(tmp_path):
    src = tmp_path / "src"
    (src / "scripts").mkdir(parents=True)
    for name, body in FILES.items():
        (src / name).write_text(body)
    (src / "scripts" / "l.py").symlink_to(src / "SKILL.md")
    with pytest.raises(ValueError, match="symbolic links"):
        trust.import_skill(REPO, folder=str(src))
    (src / "scripts" / "l.py").unlink()
    assert trust.import_skill(REPO, folder=str(src))["name"] == "go-helper"


def test_runtime_skips_unapproved_companion_with_note_and_refuses_unapproved_primary():
    trust.import_skill(REPO, zip_base64=make_zip())
    rt = MAFHarnessRuntime(api_key="k", model="m", skills_root=REPO)
    events = []
    rt._emit = lambda kind, **f: events.append((kind, f))
    st = {}
    assert rt._trusted_skills("test_generation", ["generic-test-generator", "go-helper"], st) == [
        "generic-test-generator"
    ]
    assert st["skills_skipped"][0]["note"] == "skill go-helper skipped: unverified"
    assert events[0][0] == "skill_skipped"
    with pytest.raises(ValueError, match="unverified"):
        rt._trusted_skills("test_generation", ["go-helper"], {})
    trust.approve(REPO, "go-helper")
    assert rt._trusted_skills("test_generation", ["generic-test-generator", "go-helper"], {}) == [
        "generic-test-generator",
        "go-helper",
    ]
    assert rt._skill_dir("go-helper") == (trust.plugin_dir() / "go-helper").resolve()


def test_api_lists_flags_and_import_approve_flow(tmp_path):
    api = LocalAPI(tmp_path / "runs")
    by_name = {s["name"]: s for s in api.skills()["skills"]}
    assert by_name["test-planning"]["verified"] and by_name["test-planning"]["source"] == "repo"
    assert (
        by_name["test-planning"]["stages"] == ["test_planning"]
        and by_name["test-planning"]["kind"] == "model"
    )
    imported = api.import_skill({"zip_base64": make_zip()})
    assert imported["source"] == "plugin" and not imported["verified"] and not imported["approved"]
    card = {s["name"]: s for s in api.skills()["skills"]}["go-helper"]
    assert (card["source"], card["verified"], card["approved"]) == ("plugin", False, False)
    assert card["purpose"] == "follow Go idioms" and card["stages"] == ["test_generation"]
    assert api.approve_skill("go-helper")["approved"] is True
    assert {s["name"]: s for s in api.skills()["skills"]}["go-helper"]["approved"] is True
    with pytest.raises(FileNotFoundError):
        api.approve_skill("nope")
    with pytest.raises(ValueError):
        api.import_skill({})
