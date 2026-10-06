"""Repository preview heuristics with an injected fetcher (no network)."""

import json
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

import maf_test_harness.ui.api as api_module
from maf_test_harness.repo.preview import preview_repository
from maf_test_harness.ui.api import LocalAPI, make_server


def fake(files, branch="main"):
    def fetch(op, owner, repo, ref, path=""):
        if op == "info":
            return branch
        if op == "tree":
            return list(files)
        return files.get(path)

    return fetch


URL = "https://github.com/sarveshtalele/mcp-skills-registry"
NEXT = {
    "README.md": "# Demo\n\n## Getting started\n\nRun `npm install` then `npm run dev`.\n\n## License\nMIT\n",
    "frontend/package.json": json.dumps(
        {
            "dependencies": {"next": "15", "react": "19"},
            "scripts": {"dev": "next dev", "build": "next build"},
            "engines": {"node": ">=22"},
        }
    ),
    "frontend/.nvmrc": "22\n",
    "backend/requirements.txt": "requests\n",
}


def test_next_frontend_in_subfolder():
    out = preview_repository(URL, fake(NEXT))
    assert out["app_component"] == "frontend"
    assert out["app_url"] == "http://localhost:3000"
    assert out["start_app_suggested"] is True
    assert out["detected"]["frameworks"] == ["Next.js"]
    text = out["repo_knowledge"]
    assert text.startswith("Auto-detected from GitHub (unverified)")
    assert "npm run dev" in text and "node 22" in text and "README - Getting started" in text
    assert len(text) <= 3000 and not out["warnings"]


def test_explicit_script_port_wins():
    pkg = {"scripts": {"dev": "vite --port 4000"}, "devDependencies": {"vite": "5"}}
    out = preview_repository(URL, fake({"package.json": json.dumps(pkg)}))
    assert (out["app_component"], out["app_url"]) == ("", "http://localhost:4000")


def test_fastapi_root_and_dockerfile_port():
    out = preview_repository(URL, fake({"requirements.txt": "fastapi\nuvicorn\n"}))
    assert (out["app_component"], out["app_url"]) == ("", "http://localhost:8000")
    out = preview_repository(
        URL, fake({"requirements.txt": "fastapi\n", "Dockerfile": "FROM x\nEXPOSE 9000\n"})
    )
    assert out["app_url"] == "http://localhost:9000"


def test_spring_boot():
    pom = "<project><parent>spring-boot-starter-parent</parent><java.version>21</java.version></project>"
    out = preview_repository(URL, fake({"pom.xml": pom}))
    assert out["app_url"] == "http://localhost:8080"
    assert "java 21" in out["repo_knowledge"]


def test_plain_library_has_no_app():
    out = preview_repository(
        URL, fake({"pyproject.toml": 'requires-python = ">=3.11"\n', "src/lib.py": "x=1"})
    )
    assert out["app_component"] is None and out["app_url"] is None and out["start_app_suggested"] is False
    assert out["detected"]["languages"] == ["Python"]


def test_bounded_file_count():
    files = {f"pkg{i}/package.json": "{}" for i in range(30)}
    seen = []

    def fetch(op, owner, repo, ref, path=""):
        if op == "file":
            seen.append(path)
        return fake(files)(op, owner, repo, ref, path)

    preview_repository(URL, fetch)
    assert len(seen) <= 12


def boom(*_args, **_kwargs):
    raise URLError("offline")


def test_network_failure_is_warning():
    out = preview_repository(URL, boom)
    assert out["app_component"] is None and out["warnings"]


def test_invalid_url_rejected():
    with pytest.raises(ValueError):
        preview_repository("https://example.com/a/b", fake({}))


def test_route_400_for_invalid_url_and_200_with_warnings_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "preview_repository", lambda url: preview_repository(url, boom))
    api = LocalAPI(tmp_path / "runs", requirements_mode="model")
    server = make_server(api, "test-authorization-token-32-characters", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def post(url):
        request = Request(
            f"http://127.0.0.1:{server.server_port}/repo-preview",
            json.dumps({"repo_url": url}).encode(),
            {
                "Authorization": "Bearer test-authorization-token-32-characters",
                "Content-Type": "application/json",
            },
        )
        return urlopen(request)

    try:
        with pytest.raises(HTTPError) as bad:
            post("https://example.com/x/y")
        assert bad.value.code == 400
        body = json.load(post("https://github.com/acme/demo"))
        assert body["warnings"] and body["app_component"] is None
    finally:
        server.shutdown()
        server.server_close()
        api.close()
