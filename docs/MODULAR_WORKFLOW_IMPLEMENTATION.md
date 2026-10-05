# Modular workflow and deferred execution setup

Implemented after approval, October 5, 2026. Deployment remains Next.js plus one Python backend. No existing source files or user run evidence were deleted.

## Module map

- `workflow.py`: public Harness/MAF executor, checkpoint and approval ownership, transitions and compatibility facade.
- `workflow_support.py`: stage constants, prompt policy and pure artifact helpers.
- `workflow_steps/planning.py`: model dispatch, source context, requirements/planning/design validation and static setup preview.
- `workflow_steps/generation.py`: batches, repair, traceability, static checks and deferred runtime build gate.
- `workflow_steps/execution.py`: setup approval, provisioning, strict environment guard, deferred build/collection, execution and QA.
- `ui/api.py`: loopback HTTP authentication, origin checks, run lifecycle and approval routing.
- `ui/settings_api.py`: prompt defaults/history editing and configuration.
- `ui/evidence_api.py`: skills, downloads and evidence serving.
- `ui/presentation.py`: shared presentation redaction.
- `envs/setup_guide.py`: deterministic manual guide with native command quoting and explicit unsupported setup.

## New runs

Clone approval → source review → requirements/setup preview → planning → design → generation → Test execution · Setup → execution.

`environment_setup` is retained as an internal stage ID for schemas, hooks and approvals. It now follows generation in new runs. No isolated target workspace is created during the earlier phases. Setup creates the execution source copy and requests dependency installation approval. Compilation/collection runs after execution approval, before native tests; generation still performs static gates and traceability checks. Regeneration cannot run those runtime gates before the new execution approval.

Saved runs use their persisted stage list so existing checkpoints/approval meaning does not change. Approved ready environments are reused during the existing bounded heal loop. The public Harness and LocalAPI interfaces remain available; internal test mocks now target the extracted module that owns each operation.

## Repository knowledge and unsupported setup

New run accepts at most 32,000 characters of repository knowledge. It is persisted per run, supplied as unverified model context and shown in manual guidance. It never becomes an automatically executable command. Actual source/manifests and existing dependency validation remain authoritative.

The requirements artifact and execution setup review expose a numbered guide: workspace command, missing-tool installation hints, exact supported dependency commands, component working directories, required environment variables, and retry instructions. Windows uses literal PowerShell arguments; macOS/Linux uses shell quoting. Preview command paths are translated to the future execution workspace rather than installing into the read-only clone.

If an adapter or startup procedure cannot be inferred, the guide explicitly requests missing manifests, versions, install/start commands and readiness URL. It retains pasted notes for human review. It does not invent commands or certify manual setup as ready. Source-derived installation hints do not override pinned runtime/toolchain requirements; review the displayed plan before approval.

## Verification

- Structural extraction: same 59 baseline approval/generation/environment/hook checks passed before and after the initial extraction.
- Full backend regression: 742 passed, 15 skipped. A subsequent preview-path regression and setup-guide checks passed separately.
- UI: all 7 browser workflow tests passed, including repository knowledge persistence and the setup guide at the late approval gate.
- Production build and lint/format checks passed.
- Production launcher smoke check verified the actual homepage and authenticated backend proxy, then stopped both servers.
- Windows/macOS CI remains the cross-platform gate; local verification was on macOS. Model responses in workflow regression tests use scripted transports; no fresh model-driven GitHub qualification run is claimed by these results.
