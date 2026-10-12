# PyInstaller spec for the desktop backend: `pyinstaller packaging/backend.spec --distpath build`.
# One-folder output build/backend/ with the exe and resources side by side (contents_directory="."),
# so sys._MEIPASS == the exe folder == HARNESS_RESOURCE_ROOT as set by desktop/src/main.ts.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is injected by PyInstaller)

datas = [
    (str(root / "skills"), "skills"),  # SKILLS.lock.json, plugin.yaml, scripts/, assets/, references/ ...
    (str(root / "prompts"), "prompts"),
    (str(root / "src/maf_test_harness/qa/templates"), "maf_test_harness/qa/templates"),
]
binaries = []
hiddenimports = collect_submodules("maf_test_harness")
# Required packages: collect_all raises when one is missing, so a broken build environment fails here, not at runtime.
for pkg in ("agent_framework", "agent_framework_core", "agent_framework_openai", "openai", "httpx", "pydantic", "jsonschema",
            "jsonschema_specifications"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ["yaml", "filelock"]
# Imports of the bundled skill scripts (skills/*/scripts/*.py), which run through `maf-backend --run-python` via runpy.
# PyInstaller cannot see them, so stdlib modules only they use would be missing. desktop_backend.self_check() verifies this.
hiddenimports += ["argparse", "datetime", "hashlib", "json", "pathlib", "random", "typing", "jsonschema", "yaml"]
# optional deps (MCP integration only), bundled when present in the build environment
for pkg in ("mcp",):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(  # noqa: F821
    [str(root / "src/maf_test_harness/desktop_backend.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "playwright"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="maf-backend", console=True,
          contents_directory=".")  # noqa: F821
coll = COLLECT(exe, a.binaries, a.datas, name="backend")  # noqa: F821
