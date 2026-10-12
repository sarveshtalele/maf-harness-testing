"""Every error the harness knows how to recognise: a stable code, who is at fault, and what may be done about it.

Categories tell *whose problem it is*, which decides what happens next:

* ``environment``  the isolated environment is wrong or incomplete (missing package, wrong JDK/SDK, no compiler)
* ``infra``        the machine or network (no network, disk full, port taken, permissions)
* ``test_defect``  the generated test is wrong (bad import, missing fixture, syntax): regenerate it
* ``app_defect``   the test ran and the application under test disagreed (assertion): report, never "fix"
* ``unknown``      nothing matched

Autonomy says how far the harness may go without a human: ``auto`` (apply the recipe), ``auto_once`` (one attempt),
``ask`` (needs the human's approval, e.g. a download), ``manual`` (only explain what to do).
"""

from __future__ import annotations

from dataclasses import dataclass

ENVIRONMENT, INFRA, TEST_DEFECT, APP_DEFECT, UNKNOWN = (
    "environment",
    "infra",
    "test_defect",
    "app_defect",
    "unknown",
)
AUTO, AUTO_ONCE, ASK, MANUAL = "auto", "auto_once", "ask", "manual"


@dataclass(frozen=True)
class ErrorCode:
    code: str
    category: str
    autonomy: str
    title: str
    hint: str
    recipes: tuple[str, ...] = ()
    max_attempts: int = 1


CODES: dict[str, ErrorCode] = {}


def _c(code, category, autonomy, title, hint, recipes=(), max_attempts=1):
    CODES[code] = ErrorCode(code, category, autonomy, title, hint, tuple(recipes), max_attempts)


# python
_c(
    "ENV-PY-001",
    ENVIRONMENT,
    MANUAL,
    "Package needs a C/C++ compiler",
    "A dependency has no prebuilt wheel for this Python/OS and must be compiled. Install the platform build tools "
    "(Windows: Microsoft C++ Build Tools; macOS: xcode-select --install; Linux: build-essential) or use a Python "
    "version that has wheels for the pinned versions.",
    ("prefer-binary", "install-each-package"),
)
_c(
    "ENV-PY-002",
    ENVIRONMENT,
    AUTO_ONCE,
    "No matching distribution for a requirement",
    "The pinned version does not exist for this Python/platform. Retry the package unpinned or with a compatible "
    "version.",
    ("relax-pin", "install-each-package"),
)
_c(
    "ENV-PY-003",
    ENVIRONMENT,
    ASK,
    "Python version not supported by the project",
    "The project requires a different Python. Provision a matching interpreter into the harness cache.",
    ("provision-python",),
)
_c(
    "ENV-PY-004",
    ENVIRONMENT,
    AUTO,
    "Externally managed Python",
    "The system Python refuses installs. Always install into the isolated venv.",
    ("recreate-venv",),
)
_c(
    "ENV-PY-005",
    ENVIRONMENT,
    AUTO_ONCE,
    "Dependency resolver conflict",
    "The pinned requirements conflict. Install per package and report the conflicting pins.",
    ("install-each-package", "relax-pin"),
)
_c(
    "ENV-PY-006",
    ENVIRONMENT,
    MANUAL,
    "Mixed interpreter (standard library of another Python version)",
    "A leftover interpreter directory shadows the standard library. Recreate the venv from a clean workspace.",
    ("recreate-venv",),
)
_c(
    "ENV-PY-007",
    ENVIRONMENT,
    AUTO,
    "Declared dependency is not installed",
    "The package is declared by the repository but its install failed. Tests must not import it at module level: "
    "stub it in sys.modules, or install it.",
    ("install-each-package", "stub-dependency"),
)
_c(
    "ENV-PY-011",
    ENVIRONMENT,
    MANUAL,
    "No Python interpreter for the isolated environment",
    "Install Python 3 (the version the project requires) from python.org, then retry. The desktop app does not bundle Python.",
)
_c(
    "ENV-PY-008",
    ENVIRONMENT,
    AUTO_ONCE,
    "Interpreter or venv executable not found",
    "The interpreter path does not exist (venv not created, relative path, or an alias stub). Recreate the venv "
    "with an absolute interpreter path.",
    ("recreate-venv",),
)
_c(
    "ENV-PY-009",
    ENVIRONMENT,
    AUTO,
    "Installed packages are mutually incompatible",
    "pip reports installed packages whose requirements are not met (for example fastapi pinned below starlette 0.37 "
    "next to starlette 1.x), so library calls fail with TypeError/ImportError. Reinstall the dependency at the range "
    "the dependent package declares.",
    ("fix-conflicts",),
)
_c(
    "ENV-PY-010",
    ENVIRONMENT,
    AUTO_ONCE,
    "Test client needs an extra package",
    "A test helper (for example starlette.testclient) imports a package that the harness's static tooling map did "
    "not install (httpx2 instead of httpx). Install it into the generated-test tooling only; repository pins and "
    "application dependencies are never touched.",
    ("add-test-dependency",),
)
# java
_c(
    "ENV-JAVA-001",
    ENVIRONMENT,
    ASK,
    "JDK version does not match the project",
    "The build needs another JDK release (or the class files are newer than the JDK). Provision the matching "
    "Temurin JDK into the harness cache.",
    ("provision-jdk",),
)
_c(
    "ENV-JAVA-002",
    ENVIRONMENT,
    ASK,
    "JAVA_HOME missing or not a JDK",
    "No usable JDK was found (JRE only, or JAVA_HOME wrong). Provision a JDK and point JAVA_HOME at it for the run.",
    ("provision-jdk",),
)
_c(
    "ENV-JAVA-003",
    INFRA,
    AUTO_ONCE,
    "Maven/Gradle could not resolve dependencies",
    "Dependencies could not be downloaded (network, proxy, private repository, snapshot). Retry once with update "
    "forced; if it persists, configure a mirror or provide credentials.",
    ("retry-update", "configure-mirror"),
)
_c(
    "ENV-JAVA-004",
    ENVIRONMENT,
    ASK,
    "Maven not installed",
    "Use the project's mvnw wrapper if present, otherwise provision Maven into the harness cache.",
    ("use-wrapper", "provision-maven"),
)
_c(
    "ENV-JAVA-005",
    ENVIRONMENT,
    ASK,
    "Gradle wrapper or distribution unavailable",
    "The Gradle wrapper could not run or download its distribution. Make it executable or provision Gradle.",
    ("use-wrapper", "provision-gradle"),
)
_c(
    "ENV-JAVA-006",
    ENVIRONMENT,
    ASK,
    "java executable not found",
    "There is no java on PATH. Provision a JDK into the harness cache.",
    ("provision-jdk",),
)
_c(
    "ENV-JAVA-007",
    ENVIRONMENT,
    AUTO,
    "Test framework is not on the classpath",
    "JUnit is missing from the build. Add the JUnit 5 dependency to the generated test setup only.",
    ("add-test-dependency",),
)
# dotnet
_c(
    "ENV-DOTNET-001",
    ENVIRONMENT,
    ASK,
    ".NET SDK not found",
    "No compatible .NET SDK is installed (or the global.json version is missing). Provision the SDK into the "
    "harness cache.",
    ("provision-dotnet",),
)
_c(
    "ENV-DOTNET-002",
    ENVIRONMENT,
    ASK,
    "Target framework not supported by the installed SDK",
    "The project targets a newer or older framework than the installed SDK supports. Provision the matching SDK.",
    ("provision-dotnet",),
)
_c(
    "ENV-DOTNET-003",
    INFRA,
    AUTO_ONCE,
    "NuGet package or feed unavailable",
    "A NuGet package or the package feed could not be reached or found. Retry once; check the feed, proxy and "
    "credentials.",
    ("retry-restore", "configure-mirror"),
)
_c(
    "ENV-DOTNET-004",
    ENVIRONMENT,
    AUTO,
    "Test host or test SDK missing",
    "The test project has no Microsoft.NET.Test.Sdk / runner. Add the test packages to the generated test "
    "project (never to the repository's own projects).",
    ("add-test-dependency",),
)
# common
_c(
    "ENV-COMMON-001",
    INFRA,
    AUTO_ONCE,
    "Command timed out",
    "The command exceeded its time limit. Retry once with a longer timeout; if it repeats, the step is hanging.",
    ("retry-longer",),
)
_c(
    "ENV-COMMON-002",
    INFRA,
    MANUAL,
    "Disk full",
    "The disk is full. Free space (or clear the harness cache) and retry.",
)
_c(
    "ENV-COMMON-003",
    INFRA,
    AUTO_ONCE,
    "Network unreachable",
    "The network or DNS failed. Retry once; check proxy and connectivity.",
    ("retry-longer",),
)
_c(
    "ENV-COMMON-004",
    INFRA,
    AUTO,
    "Port already in use",
    "The chosen port is taken. Pick another free port and retry.",
    ("new-port",),
)
_c(
    "ENV-COMMON-005",
    ENVIRONMENT,
    AUTO,
    "Playwright browser not installed",
    "Install the browser (npx playwright install chromium) in the isolated environment.",
    ("install-browser",),
)
_c(
    "ENV-COMMON-006",
    INFRA,
    AUTO_ONCE,
    "Nothing answers at the application URL",
    "The application under test is not running or not ready. Start it (or wait for readiness) and read the URL "
    "from BASE_URL.",
    ("start-app",),
)
_c(
    "ENV-COMMON-007",
    INFRA,
    MANUAL,
    "Path too long",
    "A path exceeds the OS limit (Windows MAX_PATH). Use a shorter run directory or enable long paths.",
)
_c(
    "ENV-COMMON-008",
    INFRA,
    MANUAL,
    "Permission denied",
    "The OS refused access (permissions, antivirus, or a locked file). Close programs using the files or choose "
    "another directory.",
)
_c(
    "ENV-COMMON-009",
    INFRA,
    MANUAL,
    "Out of memory",
    "The process ran out of memory. Reduce parallelism or raise the limit (MAVEN_OPTS, Node --max-old-space-size).",
)
_c(
    "ENV-COMMON-010",
    ENVIRONMENT,
    MANUAL,
    "Executable not found",
    "A required program is not installed or not on PATH.",
)
# execution
_c(
    "EXEC-101",
    TEST_DEFECT,
    AUTO,
    "Generated test imports something that does not exist",
    "Import only names defined in repository source files (check resolve_import); use the module path the "
    "repository really has.",
    ("fix-import-path",),
    3,
)
_c(
    "EXEC-102",
    TEST_DEFECT,
    AUTO,
    "Name, attribute or symbol error in the generated test",
    "The test uses a name that is not defined or a member that does not exist. Check module_api for the real "
    "signature.",
    (),
    3,
)
_c(
    "EXEC-103",
    APP_DEFECT,
    MANUAL,
    "Assertion failed",
    "The test ran and the application behaved differently. Check the application at the reported source line "
    "first; correct the expectation only if the application behaviour is intended.",
)
_c(
    "EXEC-104",
    TEST_DEFECT,
    AUTO,
    "Syntax or indentation error in the generated test",
    "The file does not compile. Regenerate it.",
    (),
    3,
)
_c(
    "EXEC-105",
    TEST_DEFECT,
    AUTO,
    "Fixture or setup problem in the generated test",
    "Use only built-in fixtures (monkeypatch, tmp_path, capsys) or unittest.mock; declare them as parameters.",
    (),
    3,
)
_c(
    "EXEC-106",
    TEST_DEFECT,
    AUTO_ONCE,
    "No tests were collected or executed",
    "The runner found nothing to run (wrong path, no test declaration, or all skipped). Check file naming and the "
    "test declaration.",
    (),
    2,
)
_c(
    "EXEC-107",
    TEST_DEFECT,
    AUTO,
    "Test reads from stdin",
    "A test must never read real stdin: patch input().",
    (),
    3,
)
_c(
    "EXEC-108",
    TEST_DEFECT,
    AUTO,
    "Generated test does not compile",
    "The generated test does not compile against the repository (symbol, type or signature mismatch).",
    (),
    3,
)
