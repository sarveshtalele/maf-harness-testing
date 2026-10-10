import base64
import io
import stat
import zipfile
from pathlib import Path

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


# ---- review fixes ------------------------------------------------------------------------------------------------


def drop(name="go-helper", manifest=MANIFEST, extra=None):
    d = trust.plugin_dir() / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(FILES["SKILL.md"].replace("go-helper", name))
    (d / "plugin.yaml").write_text(manifest.replace("go-helper", name))
    for rel, body in (extra or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(body)
    return d


def test_bytecode_rejected_on_import_blocked_on_approve_and_revokes_approval(tmp_path):
    with pytest.raises(ValueError, match="bytecode"):
        trust.import_skill(REPO, zip_base64=make_zip({**FILES, "scripts/__pycache__/x.cpython-312.pyc": "x"}))
    with pytest.raises(ValueError, match="bytecode"):
        trust.import_skill(REPO, zip_base64=make_zip({**FILES, "scripts/helper.pyc": "x"}))
    src = tmp_path / "src"
    for name, body in {**FILES, "scripts/__pycache__/x.pyc": "x"}.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(body)
    with pytest.raises(ValueError, match="bytecode"):
        trust.import_skill(REPO, folder=str(src))
    d = drop(extra={"scripts/__pycache__/x.pyc": b"\0"})
    with pytest.raises(ValueError, match="bytecode"):
        trust.approve(REPO, "go-helper")
    for p in d.rglob("__pycache__"):
        (p / "x.pyc").unlink()
        p.rmdir()
    trust.approve(REPO, "go-helper")
    assert trust.trust(REPO, d)["approved"]
    (d / "scripts").mkdir(exist_ok=True)
    (d / "scripts" / "__pycache__").mkdir()
    (d / "scripts" / "__pycache__" / "evil.pyc").write_bytes(b"\0")
    assert not trust.trust(REPO, d)["approved"]  # hash covers every file and bytecode is never approved


def test_approved_plugin_script_runs_isolated_without_bytecode_or_sibling_imports():
    trust.import_skill(
        REPO,
        zip_base64=make_zip(
            {
                **FILES,
                "scripts/helper.py": "VALUE = 'sibling'\n",
                "scripts/hello.py": "import helper\nprint(helper.VALUE)\n",
            }
        ),
    )
    trust.approve(REPO, "go-helper")
    out = runner().run("go-helper", "scripts/hello.py")
    assert out["exit_code"] != 0  # -I keeps the script directory off sys.path
    assert not list((trust.plugin_dir() / "go-helper").rglob("__pycache__"))


def test_zip_total_decompressed_size_is_capped_across_entries(monkeypatch):
    monkeypatch.setattr(trust, "MAX_IMPORT_BYTES", 1000)
    many = {f"data/{i}.md": "x" * 600 for i in range(5)}
    with pytest.raises(ValueError, match="too large"):
        trust.import_skill(REPO, zip_base64=make_zip({**FILES, **many}))


@pytest.mark.parametrize(
    "manifest",
    [
        MANIFEST.replace("role: companion", "role: primary").split("applies_when")[0],
        MANIFEST + "entrypoint: nonexistent_mod_xyz:run\n",
        MANIFEST.replace("kind: model", "kind: host"),
    ],
)
def test_hand_dropped_primary_host_or_entrypoint_plugin_cannot_be_approved_or_trusted(manifest):
    d = drop(manifest=manifest)
    with pytest.raises(ValueError, match="not allowed as a plugin"):  # and the entrypoint was never imported
        trust.approve(REPO, "go-helper")
    # even a forged approval record does not make it trusted or selectable
    trust.approvals_path().write_text(
        '{"approved": {"go-helper": {"sha256": "' + trust.full_tree_sha256(d) + '"}}}'
    )
    assert not trust.trust(REPO, d)["trusted"]
    assert "go-helper" not in [
        p.name for p in __import__("maf_test_harness.skills.config", fromlist=["x"]).plugins()
    ]


def test_import_rejects_primary_plugins():
    manifest = MANIFEST.replace("role: companion", "role: primary").split("applies_when")[0]
    with pytest.raises(ValueError, match="companion"):
        trust.import_skill(REPO, zip_base64=make_zip({**FILES, "plugin.yaml": manifest}))


def test_runtime_source_loads_only_the_exact_trusted_folder():
    import asyncio

    from agent_framework import SkillsSourceContext

    real = drop()
    trust.approve(REPO, "go-helper")
    decoy = (
        trust.plugin_dir() / "aaa" / "go-helper"
    )  # scanned first, same name one level down, never approved
    decoy.mkdir(parents=True)
    (decoy / "SKILL.md").write_text("---\nname: go-helper\ndescription: decoy\n---\n# decoy\n")
    rt = MAFHarnessRuntime(api_key="k", model="m", skills_root=REPO)
    provider = rt._skills_provider(["go-helper"])
    skills = asyncio.run(provider._source.get_skills(SkillsSourceContext(agent=None)))
    assert [
        Path(s.path).resolve().parent if s.path.endswith(".md") else Path(s.path).resolve() for s in skills
    ] == [real.resolve()]
    assert all(s.frontmatter.description != "decoy" for s in skills)


def test_concurrent_approvals_do_not_lose_updates():
    from concurrent.futures import ThreadPoolExecutor

    names = [f"skill-{i}" for i in range(8)]
    for n in names:
        drop(n, manifest=MANIFEST)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda n: trust.approve(REPO, n), names))
    assert set(trust.load_approvals()) == set(names)
    assert not list(trust.approvals_path().parent.glob(".plugins-*.tmp"))


def test_reimport_replaces_atomically_and_revokes_approval():
    trust.import_skill(REPO, zip_base64=make_zip())
    trust.approve(REPO, "go-helper")
    changed = {**FILES, "SKILL.md": FILES["SKILL.md"] + "more\n"}
    result = trust.import_skill(REPO, zip_base64=make_zip(changed))
    assert not result["approved"]
    assert [p.name for p in trust.plugin_dir().iterdir()] == ["go-helper"]  # no leftovers


def test_nested_zip_with_top_folder_named_like_staging_dirs_imports():
    for top in ("out", "pkg", "named", "go-helper"):
        assert trust.import_skill(REPO, zip_base64=make_zip(prefix=top + "/"))["name"] == "go-helper"


@pytest.mark.parametrize("bad", ["a:b.md", "CON.txt", "nul", "Aux.md", "COM1", "trail.", "space /x.md"])
def test_windows_hostile_names_rejected(bad):
    with pytest.raises(ValueError, match="Unsafe path"):
        trust.import_skill(REPO, zip_base64=make_zip({**FILES, bad: "x"}))


def test_runner_sees_approval_made_after_it_was_built():
    drop(extra={"scripts/hello.py": b"print('hi')\n"})
    live = runner()
    with pytest.raises(ScriptRefused):
        live.check("go-helper", "scripts/hello.py")
    trust.approve(REPO, "go-helper")
    assert live.check("go-helper", "scripts/hello.py").is_file()  # no restart needed
    (trust.plugin_dir() / "go-helper" / "plugin.yaml").write_text(MANIFEST + "# edit\n")
    with pytest.raises(ScriptRefused):
        live.check("go-helper", "scripts/hello.py")  # revocation is immediate


def test_default_approvals_store_is_outside_the_workspace(monkeypatch):
    monkeypatch.delenv("HARNESS_PLUGIN_APPROVALS")
    assert trust.approvals_path() == Path.home() / ".maf-harness" / "plugins.lock.json"
    assert not trust.approvals_path().is_relative_to(REPO.parent)
