# Test Design Generator (`test-design-generator`)

## Overview
The **Test Design Generator** skill transforms structured requirements into comprehensive test designs and test cases. It analyzes user stories, acceptance criteria, business rules, and validation rules to produce:

- **Test scenarios** and **test cases** in JSON format
- Human‑readable design reports (Markdown)
- Traceability links to requirements
- Sample data fixtures (optional)

## Input Files (placed in `input/`)

| File | Description |
|------|-------------|
| `user-story.md` | High‑level user story or feature description |
| `acceptance-criteria.md` | Acceptance criteria and validation rules |
| `validation-rules.md` | Detailed validation logic and edge‑case rules |
| `api-spec.yaml` (optional) | OpenAPI specification for API‑related stories |

## Output

The skill writes to the `output/test-design/` directory:

- `test_cases.json` – Structured test case definitions (ID, title, steps, expected result, etc.)
- `test_design_report.md` – Narrative report summarizing the generated test design
- `traceability_matrix.json` – Mapping of generated tests to source requirements
- Additional fixtures in `test-data/` if needed

## Quick Start

```bash
# Using Node script
node generate_test_design.js

# Or using Python script
python generate_test_design.py
```

Ensure you have installed the required dependencies:

```bash
npm install
pip install -r requirements.txt
```

## Configuration

You can customize the generator via environment variables:

| Variable | Description |
|----------|-------------|
| `TDG_OUTPUT_DIR` | Directory where outputs are written (default: `output/test-design`) |
| `TDG_INCLUDE_ACCEPTANCE` | Set to `false` to skip acceptance‑criteria parsing |
| `TDG_ENABLE_TRACEABILITY` | Set to `false` to omit traceability matrix generation |

## Example

**Input (`input/user-story.md`)**

```markdown
As a registered user, I want to reset my password so that I can regain access to my account.
```

**Generated Test Case (`output/test-design/test_cases.json`)**

```json
{
  "test_cases": [
    {
      "id": "TC_001",
      "title": "Reset password with valid credentials",
      "steps": [
        "Navigate to login page",
        "Enter new password that meets complexity rules",
        "Submit password reset request"
      ],
      "expected_result": "Password is reset successfully and user receives confirmation email"
    }
  ]
}
```

## Troubleshooting

- **Missing input files** – Verify that all required files exist in `input/`.
- **Permission errors** – Ensure write access to the `output/` directory.
- **No output generated** – Check that the `requirements-analysis/` folder contains parsed requirements; if empty, the input may be malformed.

## License

MIT © 2026