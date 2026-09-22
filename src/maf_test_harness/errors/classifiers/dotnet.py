""".NET: SDK, NuGet restore, test host and compile/test failures."""

from __future__ import annotations

from ._compile import compile_rules

RULES = compile_rules(
    [
        (
            "ENV-DOTNET-001",
            r"A compatible \.NET SDK was not found|The command could not be loaded, possibly because|"
            r"dotnet: command not found|'dotnet' is not recognized|No \.NET SDKs were found|"
            r"Requested SDK version: .*(?:not found|installed SDKs)",
        ),
        (
            "ENV-DOTNET-002",
            r"NETSDK1045|NETSDK1141|NETSDK1083|does not support targeting|The current \.NET SDK does not support",
        ),
        (
            "ENV-DOTNET-004",
            r"Microsoft\.NET\.Test\.Sdk|testhost\.dll|Could not find testhost|"
            r"Program does not contain a static 'Main' method|No test is available in",
        ),
        (
            "ENV-DOTNET-003",
            r"\bNU1101\b|\bNU1102\b|\bNU1103\b|\bNU1301\b|Unable to load the service index|"
            r"Response status code does not indicate success: 401|Failed to retrieve information about '",
        ),
    ]
)

EXEC_RULES = compile_rules(
    [
        ("EXEC-108", r"error CS\d+:|Build FAILED\."),
        ("EXEC-102", r"error CS0103|error CS1061|error CS0246"),
        ("EXEC-106", r"No test matches the given testcase filter|Total tests: 0"),
        ("EXEC-103", r"Assert\.\w+\(\) Failure|Expected: .*\s+Actual:|Assert\.\w+ failed"),
    ]
)
