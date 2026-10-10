# Error playbook

Every environment or execution error the harness recognises has a stable **code**, a **category** (whose problem it
is), an **autonomy** level (how far the harness may go alone) and **recipes** (what to do). The classifier lives in
`src/maf_test_harness/errors/`; the rows below are generated from it (`errors.playbook.render_markdown()`), so this page
cannot drift from the code.

## Categories

| Category | Meaning | What happens |
|---|---|---|
| `environment` | The isolated environment is wrong or incomplete (missing package, wrong JDK/SDK, no compiler) | Never counts against a generated test; the build gate reports `skipped` with the code, the QA report lists an *Environment* action |
| `infra` | The machine or network (no network, disk full, port taken, permissions, timeout) | Same as `environment`; one retry where the code allows it |
| `test_defect` | The generated test is wrong (bad import, missing fixture, syntax, compile error) | Sent back to generation with a concrete remedy (the heal loop) |
| `app_defect` | The test ran and the application disagreed (assertion) | Reported with `file:line`; the harness never weakens an assertion |

## Autonomy

`auto` apply the recipe · `auto_once` one attempt, then report · `ask` needs the human's approval (anything that
downloads software) · `manual` only explain what to do.

## How it is used today

* **Install steps** (`envs.runners.execute_setup`): a failed step carries `diagnoses` in `report.json`; the warning or
  error text names the code and the hint (`[ENV-PY-001: Package needs a C/C++ compiler. ...]`). A failed bulk `pip
  install` is retried package by package (`install-each-package`).
* **Build gate** (`tools.build_gate.try_build`): a collection failure explained by a declared-but-uninstalled package
  (`ENV-PY-007`) or by any other `environment`/`infra` diagnosis is `skipped` with `environment_issues`, not a test
  failure, so it never burns a generation retry. A genuine test defect still fails as before.
* **Execution** (`envs.runners.execute_plan`): failed setup steps and builds that produced no tests add `diagnoses`;
  `qa_report` turns environment/infra diagnoses into *Environment* actions.
* **Context decides ambiguous errors**: `No module named 'X'` is `EXEC-101` (test defect) unless `X` belongs to a
  declared dependency whose install failed (`ENV-PY-007`, environment).

Recipes named below are data; the ones marked *implemented* run today, the rest are executed by the per-language
environment adapters (next phase). The classifier is tested against **37 codes** and
over 55 recorded or representative logs (`tests/error_cases.py`).

## Error codes

| Code | Category | Autonomy | Title | Recipes |
|---|---|---|---|---|
| `ENV-PY-001` | environment | manual | Package needs a C/C++ compiler | prefer-binary, install-each-package |
| `ENV-PY-002` | environment | auto_once | No matching distribution for a requirement | relax-pin, install-each-package |
| `ENV-PY-003` | environment | ask | Python version not supported by the project | provision-python |
| `ENV-PY-004` | environment | auto | Externally managed Python | recreate-venv |
| `ENV-PY-005` | environment | auto_once | Dependency resolver conflict | install-each-package, relax-pin |
| `ENV-PY-006` | environment | manual | Mixed interpreter (standard library of another Python version) | recreate-venv |
| `ENV-PY-007` | environment | auto | Declared dependency is not installed | install-each-package, stub-dependency |
| `ENV-PY-008` | environment | auto_once | Interpreter or venv executable not found | recreate-venv |
| `ENV-PY-009` | environment | auto | Installed packages are mutually incompatible | fix-conflicts |
| `ENV-PY-010` | environment | auto_once | Test client needs an extra package | add-test-dependency |
| `ENV-JAVA-001` | environment | ask | JDK version does not match the project | provision-jdk |
| `ENV-JAVA-002` | environment | ask | JAVA_HOME missing or not a JDK | provision-jdk |
| `ENV-JAVA-003` | infra | auto_once | Maven/Gradle could not resolve dependencies | retry-update, configure-mirror |
| `ENV-JAVA-004` | environment | ask | Maven not installed | use-wrapper, provision-maven |
| `ENV-JAVA-005` | environment | ask | Gradle wrapper or distribution unavailable | use-wrapper, provision-gradle |
| `ENV-JAVA-006` | environment | ask | java executable not found | provision-jdk |
| `ENV-JAVA-007` | environment | auto | Test framework is not on the classpath | add-test-dependency |
| `ENV-DOTNET-001` | environment | ask | .NET SDK not found | provision-dotnet |
| `ENV-DOTNET-002` | environment | ask | Target framework not supported by the installed SDK | provision-dotnet |
| `ENV-DOTNET-003` | infra | auto_once | NuGet package or feed unavailable | retry-restore, configure-mirror |
| `ENV-DOTNET-004` | environment | auto | Test host or test SDK missing | add-test-dependency |
| `ENV-COMMON-001` | infra | auto_once | Command timed out | retry-longer |
| `ENV-COMMON-002` | infra | manual | Disk full | — |
| `ENV-COMMON-003` | infra | auto_once | Network unreachable | retry-longer |
| `ENV-COMMON-004` | infra | auto | Port already in use | new-port |
| `ENV-COMMON-005` | environment | auto | Playwright browser not installed | install-browser |
| `ENV-COMMON-006` | infra | auto_once | Nothing answers at the application URL | start-app |
| `ENV-COMMON-007` | infra | manual | Path too long | — |
| `ENV-COMMON-008` | infra | manual | Permission denied | — |
| `ENV-COMMON-009` | infra | manual | Out of memory | — |
| `ENV-COMMON-010` | environment | manual | Executable not found | — |
| `EXEC-101` | test_defect | auto | Generated test imports something that does not exist | fix-import-path |
| `EXEC-102` | test_defect | auto | Name, attribute or symbol error in the generated test | — |
| `EXEC-103` | app_defect | manual | Assertion failed | — |
| `EXEC-104` | test_defect | auto | Syntax or indentation error in the generated test | — |
| `EXEC-105` | test_defect | auto | Fixture or setup problem in the generated test | — |
| `EXEC-106` | test_defect | auto_once | No tests were collected or executed | — |
| `EXEC-107` | test_defect | auto | Test reads from stdin | — |
| `EXEC-108` | test_defect | auto | Generated test does not compile | — |

## Recipes

| Recipe | What it does | Downloads software |
|---|---|---|
| `install-each-package` | Retry a failed bulk install one package at a time (implemented). | no |
| `prefer-binary` | Retry with --only-binary=:all: so pip never tries to compile. | no |
| `relax-pin` | Retry one package without its exact version pin. | no |
| `recreate-venv` | Delete and recreate the isolated venv with an absolute interpreter path. | no |
| `stub-dependency` | Tell the test to stub the missing package in sys.modules instead of importing it. | no |
| `fix-import-path` | Rewrite an unambiguous import to the module path the repository really has. | no |
| `provision-python` | Download a matching CPython into the harness toolchain cache. | yes |
| `provision-jdk` | Download the Temurin JDK the project needs into the toolchain cache. | yes |
| `provision-maven` | Download Apache Maven into the toolchain cache. | yes |
| `provision-gradle` | Download the Gradle distribution into the toolchain cache. | yes |
| `provision-dotnet` | Download the .NET SDK the project needs into the toolchain cache. | yes |
| `use-wrapper` | Prefer the repository's mvnw/gradlew wrapper over a system install. | no |
| `retry-update` | Retry the build once with dependency updates forced (-U). | no |
| `retry-restore` | Retry the NuGet restore once. | no |
| `retry-longer` | Retry once with a longer timeout. | no |
| `configure-mirror` | Point the build at a corporate mirror / credentials (human supplies them). | no |
| `add-test-dependency` | Add the test framework packages to the generated test setup only. | no |
| `install-browser` | Install the Playwright browser into the isolated environment. | yes |
| `new-port` | Choose another free 127.0.0.1 port and retry. | no |
| `start-app` | Start the application under test and wait until it answers HTTP. | no |

## Adding an error

1. Add the code in `errors/taxonomy.py` (category, autonomy, title, hint, recipes).
2. Add a pattern to the matching table in `errors/classifiers/` (language table first, then `common.py`).
3. Add a log to `tests/error_cases.py` with the code it must reach; `tests/test_errors.py` requires every code to have a
   rule and at least one fixture.
