"""Recovery recipes: what each id in a diagnosis' ``recipes`` means, and whether it needs the human's consent.

The recipes are data here. Phase 3 attaches the per-language adapters that execute them (provisioning a JDK, creating a
venv...); until an adapter implements a recipe, the diagnosis' hint is what a human or agent reads.
"""

from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import CODES


@dataclass(frozen=True)
class Recipe:
    id: str
    description: str
    downloads: bool = False  # fetches software from the network: always needs consent


RECIPES: dict[str, Recipe] = {
    r.id: r
    for r in (
        Recipe("install-each-package", "Retry a failed bulk install one package at a time (implemented)."),
        Recipe("prefer-binary", "Retry with --only-binary=:all: so pip never tries to compile."),
        Recipe(
            "fix-conflicts",
            "Reinstall each conflicting dependency at the range pip check says its dependent package requires (implemented).",
        ),
        Recipe("relax-pin", "Retry one package without its exact version pin."),
        Recipe("recreate-venv", "Delete and recreate the isolated venv with an absolute interpreter path."),
        Recipe(
            "stub-dependency",
            "Tell the test to stub the missing package in sys.modules instead of importing it.",
        ),
        Recipe(
            "fix-import-path", "Rewrite an unambiguous import to the module path the repository really has."
        ),
        Recipe("provision-python", "Download a matching CPython into the harness toolchain cache.", True),
        Recipe("provision-jdk", "Download the Temurin JDK the project needs into the toolchain cache.", True),
        Recipe("provision-maven", "Download Apache Maven into the toolchain cache.", True),
        Recipe("provision-gradle", "Download the Gradle distribution into the toolchain cache.", True),
        Recipe("provision-dotnet", "Download the .NET SDK the project needs into the toolchain cache.", True),
        Recipe("use-wrapper", "Prefer the repository's mvnw/gradlew wrapper over a system install."),
        Recipe("retry-update", "Retry the build once with dependency updates forced (-U)."),
        Recipe("retry-restore", "Retry the NuGet restore once."),
        Recipe("retry-longer", "Retry once with a longer timeout."),
        Recipe(
            "configure-mirror", "Point the build at a corporate mirror / credentials (human supplies them)."
        ),
        Recipe("add-test-dependency", "Add the test framework packages to the generated test setup only."),
        Recipe("install-browser", "Install the Playwright browser into the isolated environment.", True),
        Recipe("new-port", "Choose another free 127.0.0.1 port and retry."),
        Recipe("start-app", "Start the application under test and wait until it answers HTTP."),
    )
}


def validate() -> list[str]:
    """Problems in the tables (unknown recipe ids); empty when consistent. Checked by the tests."""
    return [
        f"{code.code} references unknown recipe {recipe!r}"
        for code in CODES.values()
        for recipe in code.recipes
        if recipe not in RECIPES
    ]


def render_markdown() -> str:
    """The error catalogue as a Markdown table (source of docs/ERROR_PLAYBOOK.md)."""
    rows = [
        "| Code | Category | Autonomy | Title | Recipes |",
        "|---|---|---|---|---|",
        *(
            f"| `{c.code}` | {c.category} | {c.autonomy} | {c.title} | {', '.join(c.recipes) or '—'} |"
            for c in CODES.values()
        ),
    ]
    return "\n".join(rows)
