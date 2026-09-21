"""Recorded and representative tool output, each with the diagnosis the harness must reach.

``recorded`` = captured from a real run on this project; ``representative`` = the toolchain's documented wording.
Fields: name, source, ecosystem, text (or result), expect (primary code), optional missing (declared-but-uninstalled).
"""

from __future__ import annotations

MSVC = """  running bdist_wheel
  running build
  running build_ext
  building 'hnswlib' extension
  error: Microsoft Visual C++ 14.0 or greater is required. Get it with "Microsoft C++ Build Tools": https://visualstudio.microsoft.com/visual-cpp-build-tools/
  [end of output]

  note: This error originates from a subprocess, and is likely not a problem with pip.
  ERROR: Failed building wheel for chroma-hnswlib
ERROR: Failed to build installable wheels for some pyproject.toml based projects (chroma-hnswlib)
"""

SRE = """  File "workspace\\Python\\pythoncore-3.14-64\\Lib\\re\\_compiler.py", line 18, in <module>
    assert _sre.MAGIC == MAGIC, "SRE module mismatch"
           ^^^^^^^^^^^^^^^^^^^
AssertionError: SRE module mismatch
"""

CASES: list[dict] = [
    # ------------------------------------------------------------------------------------------- python
    {
        "name": "pip needs MSVC",
        "source": "recorded",
        "ecosystem": "python",
        "text": MSVC,
        "expect": "ENV-PY-001",
    },
    {
        "name": "pip gcc failed",
        "source": "representative",
        "ecosystem": "python",
        "text": "error: command 'gcc' failed: No such file or directory",
        "expect": "ENV-PY-001",
    },
    {
        "name": "Python.h missing",
        "source": "representative",
        "ecosystem": "python",
        "text": "fatal error: Python.h: No such file or directory\n #include <Python.h>",
        "expect": "ENV-PY-001",
    },
    {
        "name": "no matching distribution",
        "source": "representative",
        "ecosystem": "python",
        "text": "ERROR: No matching distribution found for chroma-hnswlib==0.7.3",
        "expect": "ENV-PY-002",
    },
    {
        "name": "no satisfying version",
        "source": "representative",
        "ecosystem": "python",
        "text": "ERROR: Could not find a version that satisfies the requirement torch==9.9 (from versions: none)",
        "expect": "ENV-PY-002",
    },
    {
        "name": "requires different python",
        "source": "representative",
        "ecosystem": "python",
        "text": "ERROR: Package 'numpy' requires a different Python: 3.12.4 not in '<3.12,>=3.8'",
        "expect": "ENV-PY-003",
    },
    {
        "name": "externally managed",
        "source": "representative",
        "ecosystem": "python",
        "text": "error: externally-managed-environment\n\nx This environment is externally managed",
        "expect": "ENV-PY-004",
    },
    {
        "name": "resolver conflict",
        "source": "representative",
        "ecosystem": "python",
        "text": "ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/en/latest/topics/dependency-resolution/",
        "expect": "ENV-PY-005",
    },
    {
        "name": "mixed interpreter",
        "source": "recorded",
        "ecosystem": "python",
        "text": SRE,
        "expect": "ENV-PY-006",
    },
    {
        "name": "venv interpreter missing",
        "source": "recorded",
        "ecosystem": "python",
        "result": {"status": "blocked", "error": "[WinError 2] The system cannot find the file specified"},
        "expect": "ENV-PY-008",
    },
    {
        "name": "TLS interception",
        "source": "representative",
        "ecosystem": "python",
        "text": "Could not fetch URL https://pypi.org/simple/x/: There was a problem confirming the ssl certificate: "
        "CERTIFICATE_VERIFY_FAILED",
        "expect": "ENV-COMMON-003",
    },
    {
        "name": "import error, not declared",
        "source": "recorded",
        "ecosystem": "python",
        "text": "tests/batch_05/test_x.py:3: ImportError\nE   ModuleNotFoundError: No module named 'engine'",
        "expect": "EXEC-101",
    },
    {
        "name": "import error, declared but not installed",
        "source": "recorded",
        "ecosystem": "python",
        "text": "E   ModuleNotFoundError: No module named 'fastapi'",
        "missing": ["fastapi", "chromadb"],
        "expect": "ENV-PY-007",
    },
    {
        "name": "import error, alias package",
        "source": "representative",
        "ecosystem": "python",
        "text": "E   ModuleNotFoundError: No module named 'dotenv'",
        "missing": ["python-dotenv"],
        "expect": "ENV-PY-007",
    },
    {
        "name": "syntax error",
        "source": "representative",
        "ecosystem": "python",
        "text": 'E     File "t.py", line 3\nE       def test_a(:\nE   SyntaxError: invalid syntax',
        "expect": "EXEC-104",
    },
    {
        "name": "fixture not found",
        "source": "representative",
        "ecosystem": "python",
        "text": "E       fixture 'mocker' not found",
        "expect": "EXEC-105",
    },
    {
        "name": "reads stdin",
        "source": "representative",
        "ecosystem": "python",
        "text": "OSError: pytest: reading from stdin while output is captured!",
        "expect": "EXEC-107",
    },
    {
        "name": "name error",
        "source": "representative",
        "ecosystem": "python",
        "text": "E   NameError: name 'json' is not defined",
        "expect": "EXEC-102",
    },
    {
        "name": "nothing collected",
        "source": "representative",
        "ecosystem": "python",
        "text": "collected 0 items\n\n============ no tests ran in 0.01s ============",
        "expect": "EXEC-106",
    },
    {
        "name": "assertion",
        "source": "representative",
        "ecosystem": "python",
        "text": "E       AssertionError: assert 3 == 4",
        "expect": "EXEC-103",
    },
    {
        "name": "environment beats test defect",
        "source": "representative",
        "ecosystem": "python",
        "text": "E   ModuleNotFoundError: No module named 'x'\nerror: Microsoft Visual C++ 14.0 or greater is required",
        "expect": "ENV-PY-001",
    },
    # --------------------------------------------------------------------------------------------- java
    {
        "name": "release not supported",
        "source": "representative",
        "ecosystem": "maven",
        "text": "[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.11.0:compile: "
        "Fatal error compiling: error: release version 21 not supported",
        "expect": "ENV-JAVA-001",
    },
    {
        "name": "class file too new",
        "source": "representative",
        "ecosystem": "maven",
        "text": "java.lang.UnsupportedClassVersionError: Unsupported class file major version 65",
        "expect": "ENV-JAVA-001",
    },
    {
        "name": "invalid target release",
        "source": "representative",
        "ecosystem": "maven",
        "text": "[ERROR] error: invalid target release: 17",
        "expect": "ENV-JAVA-001",
    },
    {
        "name": "gradle cannot read java version",
        "source": "representative",
        "ecosystem": "gradle",
        "text": "Could not determine java version from '21.0.1'.",
        "expect": "ENV-JAVA-001",
    },
    {
        "name": "JAVA_HOME wrong",
        "source": "representative",
        "ecosystem": "maven",
        "text": "The JAVA_HOME environment variable is not defined correctly, this environment variable is needed "
        "to run this program.",
        "expect": "ENV-JAVA-002",
    },
    {
        "name": "JRE not JDK",
        "source": "representative",
        "ecosystem": "maven",
        "text": "[ERROR] No compiler is provided in this environment. Perhaps you are running on a JRE rather than "
        "a JDK?",
        "expect": "ENV-JAVA-002",
    },
    {
        "name": "java missing",
        "source": "representative",
        "ecosystem": "gradle",
        "text": "bash: java: command not found",
        "expect": "ENV-JAVA-006",
    },
    {
        "name": "mvn missing",
        "source": "representative",
        "ecosystem": "maven",
        "text": "'mvn' is not recognized as an internal or external command",
        "expect": "ENV-JAVA-004",
    },
    {
        "name": "dependency resolution",
        "source": "representative",
        "ecosystem": "maven",
        "text": "[ERROR] Failed to execute goal on project app: Could not resolve dependencies for project "
        "com.acme:app:jar:1.0: Could not transfer artifact junit:junit:pom:4.13",
        "expect": "ENV-JAVA-003",
    },
    {
        "name": "maven parent pom",
        "source": "representative",
        "ecosystem": "maven",
        "text": "[ERROR] Non-resolvable parent POM for com.acme:app:1.0: Could not transfer artifact",
        "expect": "ENV-JAVA-003",
    },
    {
        "name": "gradle distribution",
        "source": "representative",
        "ecosystem": "gradle",
        "text": 'Exception in thread "main" java.lang.RuntimeException: Could not install Gradle distribution '
        "from 'https://services.gradle.org/distributions/gradle-8.5-bin.zip'.",
        "expect": "ENV-JAVA-005",
    },
    {
        "name": "gradlew not executable",
        "source": "representative",
        "ecosystem": "gradle",
        "text": "bash: ./gradlew: Permission denied\ngradlew: Permission denied",
        "expect": "ENV-JAVA-005",
    },
    {
        "name": "junit missing",
        "source": "representative",
        "ecosystem": "maven",
        "text": "[ERROR] /src/test/java/AppTest.java:[3,24] package org.junit.jupiter.api does not exist",
        "expect": "ENV-JAVA-007",
    },
    {
        "name": "java compile error",
        "source": "representative",
        "ecosystem": "maven",
        "text": "[ERROR] COMPILATION ERROR :\n[ERROR] /src/test/java/AppTest.java:[9,5] cannot find symbol",
        "expect": "EXEC-108",
    },
    {
        "name": "no tests run",
        "source": "representative",
        "ecosystem": "maven",
        "text": "[INFO] Tests run: 0, Failures: 0, Errors: 0, Skipped: 0",
        "expect": "EXEC-106",
    },
    {
        "name": "junit assertion",
        "source": "representative",
        "ecosystem": "maven",
        "text": "org.opentest4j.AssertionFailedError: expected: <3> but was: <4>",
        "expect": "EXEC-103",
    },
    # ------------------------------------------------------------------------------------------- dotnet
    {
        "name": "sdk missing",
        "source": "representative",
        "ecosystem": "dotnet",
        "text": "A compatible .NET SDK was not found.\n\nRequested SDK version: 8.0.100",
        "expect": "ENV-DOTNET-001",
    },
    {
        "name": "dotnet missing",
        "source": "representative",
        "ecosystem": "dotnet",
        "text": "'dotnet' is not recognized as an internal or external command",
        "expect": "ENV-DOTNET-001",
    },
    {
        "name": "framework unsupported",
        "source": "representative",
        "ecosystem": "dotnet",
        "text": "error NETSDK1045: The current .NET SDK does not support targeting .NET 9.0.",
        "expect": "ENV-DOTNET-002",
    },
    {
        "name": "nuget package not found",
        "source": "representative",
        "ecosystem": "dotnet",
        "text": "error NU1101: Unable to find package Acme.Missing. No packages exist with this id in source(s): nuget.org",
        "expect": "ENV-DOTNET-003",
    },
    {
        "name": "nuget feed down",
        "source": "representative",
        "ecosystem": "dotnet",
        "text": "error NU1301: Unable to load the service index for source https://api.nuget.org/v3/index.json.",
        "expect": "ENV-DOTNET-003",
    },
    {
        "name": "testhost missing",
        "source": "representative",
        "ecosystem": "dotnet",
        "text": "Could not find testhost.dll. Make sure Microsoft.NET.Test.Sdk is referenced.",
        "expect": "ENV-DOTNET-004",
    },
    {
        "name": "csharp compile error",
        "source": "representative",
        "ecosystem": "dotnet",
        "text": "AppTests.cs(4,7): error CS0246: The type or namespace name 'Xunit' could not be found",
        "expect": "EXEC-108",
    },
    {
        "name": "xunit assertion",
        "source": "representative",
        "ecosystem": "dotnet",
        "text": "Assert.Equal() Failure: Values differ\nExpected: 3\nActual:   4",
        "expect": "EXEC-103",
    },
    # ------------------------------------------------------------------------------------------- common
    {
        "name": "step timeout",
        "source": "recorded",
        "ecosystem": None,
        "result": {"status": "timeout"},
        "expect": "ENV-COMMON-001",
    },
    {
        "name": "disk full",
        "source": "representative",
        "ecosystem": None,
        "text": "OSError: [Errno 28] No space left on device",
        "expect": "ENV-COMMON-002",
    },
    {
        "name": "dns failure",
        "source": "representative",
        "ecosystem": None,
        "text": "curl: (6) Could not resolve host / getaddrinfo failed",
        "expect": "ENV-COMMON-003",
    },
    {
        "name": "port in use",
        "source": "representative",
        "ecosystem": None,
        "text": "OSError: [Errno 98] Address already in use",
        "expect": "ENV-COMMON-004",
    },
    {
        "name": "playwright browser missing",
        "source": "representative",
        "ecosystem": "node",
        "text": "browserType.launch: Executable doesn't exist at C:\\pw\\chromium-1187\\chrome.exe\n"
        "Looks like Playwright was just installed. Please run: npx playwright install",
        "expect": "ENV-COMMON-005",
    },
    {
        "name": "app not running",
        "source": "representative",
        "ecosystem": "node",
        "text": "page.goto: net::ERR_CONNECTION_REFUSED at http://127.0.0.1:8501/",
        "expect": "ENV-COMMON-006",
    },
    {
        "name": "path too long",
        "source": "representative",
        "ecosystem": None,
        "text": "The specified path, file name, or both are too long. The fully qualified file name must be "
        "less than 260 characters.",
        "expect": "ENV-COMMON-007",
    },
    {
        "name": "permission denied",
        "source": "representative",
        "ecosystem": None,
        "text": "PermissionError: [WinError 5] Access is denied: 'C:\\\\x\\\\y.dll'",
        "expect": "ENV-COMMON-008",
    },
    {
        "name": "out of memory",
        "source": "representative",
        "ecosystem": "node",
        "text": "FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory",
        "expect": "ENV-COMMON-009",
    },
    {
        "name": "npm missing",
        "source": "representative",
        "ecosystem": "node",
        "text": "'npm' is not recognized as an internal or external command",
        "expect": "ENV-COMMON-010",
    },
    {
        "name": "blocked step, node",
        "source": "recorded",
        "ecosystem": "node",
        "result": {"status": "blocked", "error": "[WinError 2] The system cannot find the file specified"},
        "expect": "ENV-COMMON-010",
    },
]
