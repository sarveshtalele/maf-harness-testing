# Token spend and prompt caching

Model calls are the largest cost of a run, and almost all of it is **input**. This page records the measured baseline,
how the harness now measures caching, and the rules that keep prompts cache-friendly.

## 1. Baseline (measured from `trace.jsonl` of three real runs)

| Run | Model calls | Input tokens | Output tokens | Input : output |
|---|---|---|---|---|
| reverse-engineering-skill (30 cases) | 54 | 1,213,470 | 64,096 | 19 : 1 |
| reverse-engineering-skill (second run) | 38 | 916,250 | 55,330 | 17 : 1 |
| personal-finance RAG (aborted at generation) | 18 | 210,025 | 25,023 | 8 : 1 |

Where the input goes (first run): `test_generation` 1,002,281 of 1,213,470 input tokens (**83%**, 44 calls, about 22.8k
input tokens per call), `test_design` 117,181, `test_planning` 48,124, `requirements` 45,884.

Consequence: a generation call sends about 23k tokens to produce about 1k, and most of those 23k are the same on every
call of the batch loop (persona, policies, tool schemas, skill text, repository evidence, approved design). That
repeated part is exactly what a provider **prefix cache** can serve at a fraction of the price, *if* the prompts start
with the same bytes.

## 2. What is measured now

`BudgetGuard` (`agent/runtime.py`) records for every provider call:

| Field in `trace.jsonl` `chat_call` | Meaning |
|---|---|
| `tokens.input` / `tokens.output` | provider-reported usage |
| `tokens.cached` | input tokens the provider reports as served from its prompt cache (`cache_read_input_token_count`; `null` when the provider does not report it) |
| `prompt_chars` | size of the serialized request (messages plus tool schemas) |
| `prefix_shared_chars` | how many leading characters are identical to the previous request of the same stage |

`monitoring.report.build_report` turns them into a `cache` section and three totals (`input_tokens`,
`cached_input_tokens`, `cache_hit_pct`). The Markdown export and `maf-harness trace` show a **Prompt cache** table per
stage:

* **Hit %** is what the provider actually served from cache (only when it reports it).
* **Prompt prefix repeated %** is the ceiling: the share of each request that repeats the previous one byte for byte.
  If this is low, caching cannot help no matter what the provider supports; if it is high but Hit % is low, the
  gateway or model is not caching (check that the request is above the provider's minimum, usually 1,024 tokens).

The gateway used here (`deepseek-v4-flash` behind LiteLLM) caches identical prompt prefixes automatically, so no request
flag is needed; the measurement above tells you whether it is working.

## 3. Rules for cache-friendly prompts

1. **Order is stable-first**: harness rules and persona, then tool schemas (fixed order), then skill adverts, then
   stage context (repository evidence, approved artifacts), and only then the per-call part (batch cases, repair
   feedback). Anything that changes per call goes last.
2. **Byte-stable serialization** of the stable part: sorted keys, no timestamps, no random IDs, no run-specific paths.
3. **Reuse the session** across calls of one stage so earlier turns are a prefix of later ones instead of being
   resent inside a new prompt.
4. **Do not resend what a tool can fetch**: bulky evidence is read on demand, tool results stay bounded.
5. **Fewer calls beat cheaper calls**: environment failures never trigger regeneration; validators run as tools
   inside the loop.

## 4. How to check a run

```bash
uv run maf-harness trace .maf-harness/runs/<run>          # Prompt cache table per stage
```

Compare `Hit %` and `Prompt prefix repeated %` per stage with the baseline above. The planned target for generation
stages is at least 60% of input tokens served from cache.

## Prompt assembly (Phase 5)

Stage prompts are built stable-first (`llm/prompts.py`): assignment, output schema and rules before the per-call content, so
consecutive calls of a stage share the longest possible prefix. Measured on the fixture repository, the repeated share of a
generation prompt went from 60.7 % to 82.2 %. On a real repository the effect is much smaller (8.3 % repeated prefix in a live run, because per-batch source and evidence dominate the prompt; see [audit/E2E_RESULTS.md](audit/E2E_RESULTS.md) §8). What was deliberately not changed (session reuse, priming, compaction) and why:
[AUTOMATION.md](AUTOMATION.md#3-prompt-assembly-for-the-providers-prefix-cache).

## Strict generation update — October 6, 2026

[Current intake, batching, Java quality gates and scoped QA](PETSTORE_STRICT_TEST_GENERATION.md) supersede earlier truncation and final-retry acceptance behavior. New deterministic requirements retain all discovered declarations; installation/runtime checks remain at Test execution. Maven baseline unit/integration and generated results are separate, and runtime coverage is explicitly unmeasured. Run logs default to the checkout’s `.maf-harness` directory.

## Batch-independent schema and the local response cache

**Prefix cache.** The test-design `output_contract` no longer embeds the per-batch requirement-ID `enum`; the allowed IDs
are only in the late `approved_traceability_ids` section (host validation in `finalize_design` still rejects unknown IDs).
`STAGE_ORDER` / `GENERATION_ORDER` put every section that is identical across batches of a stage before any per-batch
section (`approved_inputs`, `repository_evidence`, `existing_tests`, `testability`, `approved_traceability_ids`). On the
fixture, two design batches now share 5183 leading chars instead of 2733 (everything before `approved_inputs`). Design
`repository_evidence` for a batch is capped at 14k chars (was 40k), preferring the batch's own source files.

**Response cache** (`agent/runtime.py`, `MAFHarnessRuntime.run_stage`). A stage answer that the host *accepted* is stored as
JSON under `HARNESS_RESPONSE_CACHE_DIR` (default `<repo-root>/.maf-harness/cache/responses`), keyed by
sha256(model, stage, skills, persona, prompt, response-format schema, temperature, skill mode). Only `temperature == 0`
calls are cached, so repair/retry attempts (temperature > 0) always call the provider. The host calls
`settle_response_cache(stage, validated)` after its own validation: accepted fresh answers are written, rejected answers
are never written, and a cached answer the host rejects is deleted. A hit goes through the same validators, records a
`cache_hit` trace event (stage, `prompt_sha256`, zero tokens) and spends no provider tokens or daily budget.
`HARNESS_RESPONSE_CACHE=0` disables it. Not cached: the independent test reviewer and tool agents (their tools read live
state that is not part of the key). Delete the directory to clear it.
