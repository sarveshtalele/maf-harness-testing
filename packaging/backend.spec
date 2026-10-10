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
for pkg in ("agent_framework", "agent_framework_core", "agent_framework_openai", "openai", "pydantic", "jsonschema",
            "jsonschema_specifications"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # package not installed in this env
        continue
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ["yaml", "filelock"]
# optional deps, bundled when present in the build environment
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
