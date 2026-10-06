"""Source-backed Maven regression scopes, separate from generated cases."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path


def maven_regression_steps(workspace, files, mvn, cache, timeout):
    generated = {file["path"] for file in files}
    unit, integration = [], []
    for path in sorted((workspace / "src/test/java").rglob("*.java")):
        if path.is_symlink() or path.relative_to(workspace).as_posix() in generated:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"@Test\b|extends\s+TestCase\b", text):
            continue
        target = (
            integration
            if re.search(r"@RunWith\s*\(\s*Arquillian\s*\.\s*class\s*\)", text)
            or path.stem.endswith("IT")
            or path.stem.startswith("IT")
            else unit
        )
        target.append(path.stem)
    common = [*mvn, "-B", "-ntp", f"-Dmaven.repo.local={Path(cache).as_posix()}"]
    steps, expectations, notes = [], {}, []
    if unit:
        steps.append(
            {
                "name": "existing-unit-regression",
                "argv": [*common, "test", "-Dtest=" + ",".join(unit), "-DfailIfNoTests=true"],
                "parser": "junit",
                "report_glob": "**/target/surefire-reports/TEST-*.xml",
                "timeout": timeout,
                "scope": "baseline_unit",
            }
        )
    expectations["baseline_unit"] = {"classes": unit, "status": "planned" if unit else "not_applicable"}
    if integration:
        try:
            pom = ET.parse(workspace / "pom.xml").getroot()
            managed = any(
                (p.findtext("{*}id") or "") == "arquillian-wildfly-managed"
                for p in pom.findall(".//{*}profile")
            )
        except (OSError, ET.ParseError):
            managed = False
        if managed:
            steps.append(
                {
                    "name": "existing-integration-regression",
                    "argv": [
                        *common,
                        "verify",
                        "-Parquillian-wildfly-managed",
                        "-DskipITs=false",
                        "-Dit.test=" + ",".join(integration),
                        *(["-Dtest=" + ",".join(unit)] if unit else []),
                        "-DfailIfNoTests=true",
                    ],
                    "parser": "junit",
                    "report_glob": "**/target/failsafe-reports/TEST-*.xml",
                    "timeout": timeout,
                    "scope": "baseline_integration",
                }
            )
        reason = (
            "Repository-managed Arquillian profile; may download/start WildFly during approved execution."
            if managed
            else "Existing integration tests need repository-specific runtime configuration; no supported managed profile was found."
        )
        expectations["baseline_integration"] = {
            "classes": integration,
            "status": "planned" if managed else "not_planned",
            "reason": reason,
        }
        notes.append(reason)
    else:
        expectations["baseline_integration"] = {"classes": [], "status": "not_applicable"}
    return steps, expectations, notes
