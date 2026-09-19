# Test Design Generator - Reference Guide

## Table of Contents

- [Overview](#overview)
- [Output Structure Reference](#output-structure-reference)
- [Test Cases by Type](#3-test-cases-organized-by-type)
- [Traceability Matrix](#4-traceability-matrix)
- [Coverage Analysis](#5-coverage-analysis)
- [Gaps Report](#6-gaps-report)
- [Integration Scenarios](#integration-scenarios)
- [Best Practices](#best-practices-for-using-enhanced-output)
- [Performance Considerations](#performance-considerations)
- [Error Handling](#error-handling)

## Overview

This reference guide provides information on using the enhanced Test Design Generator output for test case generation, coverage analysis, traceability, and quality reporting.

## Output Structure Reference

### 1. Metadata Section
Provides context about test generation:
```json
{
  "metadata": {
    "generated_at": "ISO 8601 timestamp",
    "generator_version": "1.0",
    "input_sources": ["list of processed input files"],
    "tool": "test-design-generator"
  }
}
```

**Use Cases:**
- Track test generation history and version
- Audit which inputs were used for generation
- Enable reproducible test generation

### 2. Summary Section
High-level statistics and coverage overview:

| Field | Description | Example |
|-------|-------------|---------|
| `total_tests_generated` | Total count across all types | 12 |
| `positive_tests_count` | Happy-path scenarios | 3 |
| `negative_tests_count` | Error scenarios | 6 |
| `boundary_tests_count` | Boundary value tests | 2 |
| `validation_tests_count` | Data validation tests | 1 |
| `contract_tests_count` | API contract tests | 0 |
| `coverage_percentage` | Requirements with ≥1 test | 100 |
| `requirements_covered` | Number of covered requirements | 5 |
| `total_requirements` | Total input requirements | 5 |

**Use Cases:**
- Dashboard display of test generation metrics
- Automated quality gates (fail if coverage < 100%)
- Progress tracking during test development
- Report generation for stakeholders

### 3. Test Cases (Organized by Type)

#### Positive Tests
Happy-path scenarios demonstrating successful functionality.

**Structure:**
```json
{
  "id": "TC-001",
  "title": "Brief test title",
  "type": "positive",
  "priority": "P1",
  "description": "Detailed description of what is being tested",
  "expected_result": "What should happen",
  "traceability_links": [
    {"type": "requirement", "id": "REQ-001"}
  ]
}
```

`priority` is required: use `P0` only when source evidence identifies release-blocking, security, payment, or data-loss risk; use `P1` for core behavior and validations; use `P2` otherwise. Record a defaulted priority decision in `gaps_report.notes` when the source does not state criticality.

#### Negative Tests
Error scenarios, invalid inputs, and edge cases.

**Structure:**
```json
{
  "id": "TC-002",
  "title": "Brief test title",
  "description": "Detailed error scenario description",
  "expected_result": "Error message or status code",
  "traceability_links": [
    {"type": "validation", "id": "VAL-001"}
  ]
}
```

#### Boundary Tests
Boundary value analysis for numeric ranges, string lengths, dates.

**Structure:**
```json
{
  "id": "TC-009",
  "title": "Boundary test title",
  "boundary_condition": "Description of boundary being tested",
  "test_data": {"field": "value at boundary"},
  "traceability_links": [
    {"type": "validation", "id": "VR-001"}
  ]
}
```

#### Validation Tests
Data validation, format validation, constraint checking.

**Structure:**
```json
{
  "id": "TC-011",
  "title": "Validation test title",
  "validation_target": "What is being validated",
  "test_scenarios": [
    {"missing_field": "name", "expected_error": "name is required"}
  ],
  "traceability_links": [
    {"type": "validation", "id": "VAL-003"}
  ]
}
```

#### Contract Tests
API contract compliance and response validation.

**Structure:**
```json
{
  "id": "TC-005",
  "title": "Contract test title",
  "endpoint": "/api/endpoint",
  "method": "POST",
  "expected_status_codes": ["201", "400", "401"],
  "traceability_links": [
    {"type": "api_contract", "id": "API-001"}
  ]
}
```

### 4. Traceability Matrix
Bidirectional mapping between tests and source artifacts.

**Structure:**
```json
{
  "traceability_matrix": {
    "requirement_to_tests": {
      "REQ-001": ["TC-001", "TC-002"],
      "REQ-002": ["TC-003"]
    },
    "validation_to_tests": {
      "VAL-001": ["TC-002", "TC-011"],
      "VAL-002": ["TC-009"]
    },
    "api_to_tests": {
      "API-001": ["TC-005"]
    }
  }
}
```

**Use Cases:**
- Impact analysis: "Which tests are affected by requirement changes?"
- Coverage verification: "Which requirements have tests?"
- Test selection: "Run tests for requirement REQ-001"
- Regression planning: "What tests cover validation VAL-002?"

### 5. Coverage Analysis
Detailed per-artifact coverage showing test count and coverage status.

**Structure:**
```json
{
  "coverage_analysis": {
    "requirement_coverage": {
      "REQ-001": {
        "covered": true,
        "test_count": 2,
        "test_ids": ["TC-001", "TC-002"]
      }
    },
    "validation_coverage": {
      "VAL-001": {
        "covered": true,
        "test_count": 1,
        "test_ids": ["TC-002"]
      }
    }
  }
}
```

**Use Cases:**
- Coverage reporting by requirement
- Identifying partially covered requirements (< 2 tests)
- Finding uncovered requirements (covered: false)
- Test distribution analysis per artifact

### 6. Gaps Report
Identifies missing coverage and incomplete items.

**Structure:**
```json
{
  "gaps_report": {
    "missing_validations": ["VAL-005"],
    "incomplete_requirements": ["REQ-003"],
    "coverage_gaps": ["No negative tests for REQ-003"],
    "uncovered_requirements": ["REQ-004"],
    "uncovered_validations": [],
    "notes": "Recommendation: Add error scenario tests for REQ-003"
  }
}
```

**Use Cases:**
- Quality gate verification (fail if gaps exist)
- Priority-based test development
- Coverage improvement tracking
- Risk assessment (uncovered areas)

## Integration Scenarios

### 1. Test Execution Reporting
```
Summary: 12 tests generated (3 positive, 6 negative, 2 boundary, 1 validation)
Coverage: 100% (5/5 requirements covered)
Status: READY FOR EXECUTION
```

### 2. Quality Metrics Dashboard
```
Metric               Value    Status
─────────────────────────────────────
Total Tests          12       ✓
Coverage             100%     ✓
Positive:Negative    1:2      ✓ (balanced)
Uncovered Items      0        ✓
```

### 3. Traceability Reporting
```
Requirement REQ-001:
  ├─ TC-001 (positive)
  └─ TC-002 (negative)

Validation VAL-001:
  ├─ TC-002 (negative)
  └─ TC-011 (validation)
```

### 4. Risk Assessment
```
High Risk (0 tests): None
Medium Risk (<2 tests):
  - REQ-004 (1 test)
Low Risk (≥2 tests):
  - REQ-001 (2 tests)
  - REQ-002 (2 tests)
```

## Best Practices for Using Enhanced Output

1. **Coverage Verification**
   - Check `coverage_percentage` before proceeding
   - Review `gaps_report` for incomplete items
   - Target 100% requirement coverage

2. **Test Distribution**
   - Aim for at least 1 positive + 1 negative per requirement
   - Include boundary tests for constraints
   - Balance test type distribution

3. **Traceability Maintenance**
   - Use traceability_matrix for impact analysis
   - Keep tests linked to source artifacts
   - Enable bidirectional traceability

4. **Coverage Analysis**
   - Regularly review coverage_analysis for gaps
   - Prioritize uncovered requirements
   - Track coverage trends over time

5. **Reporting**
   - Use summary metrics for stakeholder updates
   - Generate coverage reports from coverage_analysis
   - Document gaps and recommendations

## Performance Considerations

- Summary generation should complete in <1 second per 100 tests
- Traceability matrix computation should be O(n) where n = number of tests
- Coverage analysis should complete in linear time
- Gaps report generation uses set operations for efficiency

## Error Handling

When gaps are detected:
```json
{
  "gaps_report": {
    "missing_validations": ["VAL-004"],
    "notes": "WARNING: Validation rule VAL-004 has no test coverage. Recommendation: Generate validation test for VAL-004"
  }
}
```

When incomplete requirements exist:
```json
{
  "gaps_report": {
    "incomplete_requirements": ["REQ-003"],
    "coverage_gaps": ["REQ-003 has only 1 test. Recommend adding 1 negative test."],
    "notes": "ACTION REQUIRED: Improve coverage for REQ-003"
  }
}
```
