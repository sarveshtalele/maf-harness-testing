---
name: traceability-generator
description: Build exact source-to-story-to-control-to-requirement-to-plan-to-case links and gaps.
---

# Static traceability matrix

Adapted from MAF `skills/traceability-generator`. Read existing IDs and links; do not create a relationship because two titles look alike. Reject duplicate or malformed IDs, resolve each foreign key exactly, and deduplicate repeated links. For every source, story, AC/control, FR/TR, plan and case, report a valid path to a case or an ID-linked gap. A case without a valid requirement/control link is orphaned. A broken back-reference is a blocker, never a covered row.

The plugin generates `traceability.csv` from validated links. It does not link to generated source files because this workflow generates no executable code. Label the matrix as static design traceability; never claim it proves runtime, line, branch or mutation coverage. Review both forward and reverse paths before final approval.
