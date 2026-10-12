import sys
from pathlib import Path

from fakes import children_import_this_checkout

from maf_test_harness import desktop_backend

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import build_desktop  # noqa: E402


def test_plan_order_and_arch():
    steps = build_desktop.plan("arm64")
    assert steps[0]["argv"][:3] == ["uv", "run", "pyinstaller"]
    work = steps[0]["argv"][steps[0]["argv"].index("--workpath") + 1]
    assert work != "build/backend"  # PyInstaller refuses a workpath inside the dist output
    assert steps[2]["env"] == {"DESKTOP_BUILD": "1"} and steps[2]["after"] == "copy_static"
    assert steps[-1]["argv"][-2:] == ["--", "--arm64"]
    assert "--" not in build_desktop.plan()[-1]["argv"]


def test_dry_run_prints_without_executing(capsys, monkeypatch):
    monkeypatch.setattr(
        build_desktop.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    assert build_desktop.main(["--dry-run", "--arch", "x64"]) == 0
    out = capsys.readouterr().out
    assert "pyinstaller packaging/backend.spec" in out and "DESKTOP_BUILD=1" in out and "--x64" in out


def test_backend_delegates_port_to_api(monkeypatch):
    seen = []
    from maf_test_harness.ui import api

    monkeypatch.setattr(api, "main", lambda: seen.append(sys.argv[1:]))
    assert desktop_backend.main(["--port", "9001"]) == 0
    assert seen == [["--port", "9001"]]


def test_self_check_on_source_tree(monkeypatch):
    monkeypatch.delenv("HARNESS_RESOURCE_ROOT", raising=False)
    children_import_this_checkout(monkeypatch)
    assert desktop_backend.main(["--self-check"]) == 0


def test_stage_frontend_renames_node_modules_to_deps(tmp_path, monkeypatch):
    import build_desktop

    standalone = tmp_path / "standalone"
    (standalone / "node_modules" / "next").mkdir(parents=True)
    (standalone / ".next").mkdir()
    (standalone / "server.js").write_text("", encoding="utf-8")
    monkeypatch.setattr(build_desktop, "STAGED_FRONTEND", tmp_path / "out" / "frontend")
    staged = build_desktop.stage_frontend(standalone)
    assert sorted(p.name for p in staged.iterdir()) == [".next", "deps", "server.js"]
    assert (staged / "deps" / "next").is_dir() and (standalone / "node_modules").is_dir()
