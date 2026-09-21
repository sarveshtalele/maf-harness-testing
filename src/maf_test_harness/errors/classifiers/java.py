"""Java (Maven and Gradle): toolchain, dependency resolution and compile/test failures."""

from __future__ import annotations

from ._compile import compile_rules

RULES = compile_rules(
    [
        (
            "ENV-JAVA-001",
            r"release version \d+ not supported|invalid target release|Unsupported class file major version|"
            r"UnsupportedClassVersionError|class file has wrong version|invalid source release|"
            r"Source option \d+ is no longer supported|Target option \d+ is no longer supported|"
            r"compiled by a more recent version of the Java Runtime|Could not determine java version from|"
            r"incompatible Java",
        ),
        (
            "ENV-JAVA-002",
            r"JAVA_HOME (?:environment variable )?is not defined|JAVA_HOME is set to an invalid|"
            r"No compiler is provided in this environment|running on a JRE rather than a JDK",
        ),
        (
            "ENV-JAVA-006",
            r"java: command not found|'java' is not recognized|java(?:\.exe)?: not found|Cannot run program \"java\"",
        ),
        ("ENV-JAVA-004", r"mvn: command not found|'mvn' is not recognized|mvn(?:\.cmd)?: not found"),
        (
            "ENV-JAVA-005",
            r"Could not install Gradle distribution|gradlew: Permission denied|Unable to download gradle|"
            r"Downloading https://services\.gradle\.org.*(?:failed|Exception)",
        ),
        (
            "ENV-JAVA-007",
            r"package org\.junit(?:\.jupiter(?:\.api)?)? does not exist|package org\.testng does not exist",
        ),
        (
            "ENV-JAVA-003",
            r"Could not resolve dependencies|Could not transfer artifact|Non-resolvable parent POM|"
            r"Could not resolve all files for configuration|Could not GET '|Failed to read artifact descriptor|"
            r"Plugin .* or one of its dependencies could not be resolved",
        ),
    ]
)

EXEC_RULES = compile_rules(
    [
        (
            "EXEC-106",
            r"No tests to run|No tests were executed|Tests run: 0,|No tests found for given includes",
        ),
        (
            "EXEC-108",
            r"COMPILATION ERROR|Compilation failure|error: incompatible types|error: cannot find symbol|"
            r"error: method .* cannot be applied|has private access",
        ),
        ("EXEC-102", r"cannot find symbol|cannot be applied to"),
        ("EXEC-103", r"AssertionError|expected: ?<|ComparisonFailure|Assertions?\.\w+.*failed"),
    ]
)
