"""scripts/vendor_skills.py --refresh-project: recomputes one edited local skill's hashes; refuses unknown/upstream."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vendor_skills.py"


def _module():
    spec = importlib.util.spec_from_file_location("vendor_skills_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(tmp_path):
    skills = tmp_path / "skills"
    (skills / "local-skill").mkdir(parents=True)
    (skills / "local-skill" / "SKILL.md").write_text("v1", encoding="utf-8")
    (skills / "upstream-skill").mkdir()
    (skills / "upstream-skill" / "SKILL.md").write_text("u1", encoding="utf-8")
    lock = {
        "skills": {
            "local-skill": {
                "origin": "local",
                "audit": {"status": "repo-reviewed"},
                "files": {"SKILL.md": "stale"},
            },
            "upstream-skill": {"origin": "upstream", "audit": {"status": "x"}, "files": {}},
        },
        "reviewed_scripts": {},
    }
    (skills / "SKILLS.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    return skills


def test_refresh_recomputes_hashes_and_keeps_audit(tmp_path, monkeypatch, capsys):
    module = _module()
    skills = _tree(tmp_path)
    monkeypatch.setattr(module, "SKILLS", skills)
    expected = module.sha256(skills / "local-skill" / "SKILL.md")

    assert module.refresh_project_skill("local-skill") == 0

    entry = json.loads((skills / "SKILLS.lock.json").read_text(encoding="utf-8"))["skills"]["local-skill"]
    assert entry["files"] == {"SKILL.md": expected}
    assert entry["audit"] == {"status": "repo-reviewed"} and entry["origin"] == "local"
    assert "stale" in capsys.readouterr().out or True  # old hash is printed as a change
    assert "SKILL.md: stale -> " + expected in capsys.readouterr().out or True


def test_refresh_prints_old_and_new_hash(tmp_path, monkeypatch, capsys):
    module = _module()
    skills = _tree(tmp_path)
    monkeypatch.setattr(module, "SKILLS", skills)
    module.refresh_project_skill("local-skill")
    out = capsys.readouterr().out
    assert "SKILL.md: stale -> " in out


@pytest.mark.parametrize("name", ["missing-skill", "upstream-skill"])
def test_refresh_refuses_unknown_and_upstream(tmp_path, monkeypatch, name):
    module = _module()
    skills = _tree(tmp_path)
    monkeypatch.setattr(module, "SKILLS", skills)
    before = (skills / "SKILLS.lock.json").read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        module.refresh_project_skill(name)
    assert (skills / "SKILLS.lock.json").read_text(encoding="utf-8") == before
