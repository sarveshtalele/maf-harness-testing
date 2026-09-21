"""Errors that look the same in every ecosystem: machine, network, ports, browsers, permissions."""

from __future__ import annotations

from ._compile import compile_rules

RULES = compile_rules(
    [
        (
            "ENV-COMMON-001",
            r"timed out after \d+(?:\.\d+)? seconds|subprocess\.TimeoutExpired|Command timed out",
        ),
        ("ENV-COMMON-002", r"No space left on device|There is not enough space on the disk|\bENOSPC\b"),
        (
            "ENV-COMMON-005",
            r"Executable doesn't exist at|npx playwright install|BrowserType\.launch:|"
            r"download new browsers",
        ),
        (
            "ENV-COMMON-004",
            r"Address already in use|\bEADDRINUSE\b|Only one usage of each socket address|port \d+ is already in use",
        ),
        (
            "ENV-COMMON-006",
            r"ERR_CONNECTION_REFUSED|\bECONNREFUSED\b|Connection refused|ConnectionRefusedError|"
            r"actively refused it",
        ),
        (
            "ENV-COMMON-003",
            r"Temporary failure in name resolution|getaddrinfo failed|Name or service not known|"
            r"Network is unreachable|\bECONNRESET\b|\bETIMEDOUT\b|Could not fetch URL|CERTIFICATE_VERIFY_FAILED|"
            r"\bSSLError\b|\bProxyError\b|Max retries exceeded with url|Failed to establish a new connection|"
            r"Connection timed out",
        ),
        (
            "ENV-COMMON-007",
            r"Filename too long|The specified path, file name, or both are too long|\bENAMETOOLONG\b|path too long",
        ),
        (
            "ENV-COMMON-009",
            r"\bMemoryError\b|java\.lang\.OutOfMemoryError|JavaScript heap out of memory|Cannot allocate memory",
        ),
        (
            "ENV-COMMON-008",
            r"Permission denied|Access is denied|\bPermissionError\b|\bEACCES\b|\bEPERM\b|"
            r"being used by another process",
        ),
        (
            "ENV-COMMON-010",
            r"command not found|is not recognized as an internal or external command|\[WinError 2\]|"
            r"ENOENT: no such file|FileNotFoundError: \[Errno 2\]",
        ),
    ]
)

EXEC_RULES = compile_rules(
    [
        ("EXEC-106", r"no tests found|0 tests? (?:passed|found)|Tests:\s+0 total"),
        ("EXEC-103", r"\bexpect\(.*\)\.(?:toBe|toEqual|toHaveText|toBeVisible)|Expected .* Received"),
    ]
)
