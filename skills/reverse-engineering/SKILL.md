---
name: reverse-engineering
description: Deterministically inventory source declarations from an approved local clone with bounded, contained reads, source hashes, evidence lines and explicit parser gaps. Does not execute repository code or call models.
compatibility: Agentic QE Platform Python host on Windows and macOS.
metadata:
  execution: host-static
---
# Reverse engineering

The host calls `maf_test_harness.repo.reverse_engineering.reverse_engineer` after clone approval. This is an architecture-specific adaptation of sarveshtalele/reverse-engineering-skill, reviewed at commit e6139c314e3c89d2e84efe690a8bdfbce3dc17d2.

Python declarations use AST; non-Python declaration candidates are explicitly heuristic. Read only contained regular source files; skip generated/dependency directories by exact path segment, not substring. Bound file count, file size and total bytes. Preserve original line numbers and content hashes. Do not follow source symlinks, clone again, run target scripts, install dependencies, create target environments or invoke a model.

Keep candidate facts separate from demonstrated semantics. Never claim architectural pattern, domain behavior, security, compliance or complete coverage from symbol names. Record syntax failures, skipped files and limits in gaps. Retain the existing host's route, dependency and stack evidence rather than replacing those contracts.

Output: JSON facts, files, coverage and gaps, stored inside the repository-analysis artifact and `reverse_engineering.json`.

Execution proof: the host runs `scripts/analyze.py --request input/request.json --output output.json` through its reviewed host-tier script runner. The request is host-owned JSON; reverse engineering reads the approved clone, and requirements consumes its saved facts. The runner records script SHA-256, stage, exit code and duration. Successful JSON output is validated before the review gate. No model chooses or invokes these two scripts.
