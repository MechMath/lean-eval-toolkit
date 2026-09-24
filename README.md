# Lean Eval Toolkit

[中文说明](README_ch.md)

Lean Eval Toolkit evaluates the mathematical theorem-proving ability of remote language models
on Lean 4 benchmarks. It sends each problem to an OpenAI-compatible chat-completions endpoint,
extracts a complete Lean source file, and verifies the candidate with
[AXLE (Axiom Lean Engine)](https://github.com/AxiomMath/axiom-lean-engine).

Starting or hosting the model is outside this project's scope. The endpoint may be a local model
served by vLLM or a compatible commercial API such as DeepSeek, OpenAI, or OpenRouter.

## Features

- Versioned miniF2F and PutnamBench JSONL snapshots committed in `data/`.
- Per-problem Lean environments; benchmark versions never depend on one global `.env` value.
- Importers for benchmark checkouts, JSONL files, individual `.lean` files, and Lean directories.
- Concurrent generation, multiple attempts, problem-ID and split filters, and empirical pass@k.
- Strict AXLE `verify_proof` acceptance with no permitted `sorry` declarations.
- Streamed run artifacts containing model output, token usage, errors, and verification details.

## Included datasets

| Dataset | Tasks | Split | Lean environment |
| --- | ---: | --- | --- |
| miniF2F | 498 | 256 validation (unverified), 242 test | test: `lean-4.30.0`; validation: `lean-4.27.0` |
| PutnamBench | 672 | test | `lean-4.30.0` |

Exact upstream commits, authorship, licenses, extraction rules, and SHA-256 digests are recorded in
[`data/README.md`](data/README.md) and each dataset's `PROVENANCE.md`. The snapshots contain
benchmark statements with `sorry` placeholders, not generated answers or reference proofs.

## Installation

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync --all-groups
cp .env.example .env
```

## Configuration

Non-sensitive defaults are stored in
[`conf/default.yaml`](src/lean_eval_toolkit/conf/default.yaml). It contains the `model`, `axle`,
`retry`, and `evaluation` sections and imports the independent dataset registry with:

```yaml
includes:
  - datasets.yaml
```

Built-in dataset descriptions, layouts, split environments, and import-module policies live in
[`conf/datasets.yaml`](src/lean_eval_toolkit/conf/datasets.yaml). OmegaConf merges included YAML
files before Pydantic validates the complete structure.

Evaluation test templates are separate from model/tokenizer chat templates. The former render
benchmark fields into OpenAI-compatible `messages` and extract Lean from the response. The latter
are provider/tokenizer Jinja templates responsible for BOS/EOS and role special tokens. The
toolkit sends an explicitly configured `model.chat_template` to the model endpoint but never uses
it to render benchmark text itself.

The default test template is the built-in `lean-cot-v1` definition at
[`conf/test_templates/lean-cot-v1.yaml`](src/lean_eval_toolkit/conf/test_templates/lean-cot-v1.yaml):

```yaml
model:
  chat_template: null  # optional provider/tokenizer template

evaluation:
  test_template: lean-cot-v1  # built-in ID or YAML path
```

For v3 Plan-and-Repair checkpoints, select `evaluation.test_template: lean-plan-repair-v3`
(the bundled WuProver config selects it). Its initial user message asks for a proof plan followed
by the completed theorem declaration, with the formal statement in a `lean4` fence and no
repeated imports. The strict response format is `### Proof Plan`, then `### Lean Proof` with a
closed, terminal `lean4` fence. Relaxed extraction remains available, and each result records
the selected strategy and fallback flag. `lean-cot-v1` remains available for older checkpoints.

To evaluate compiler-feedback repair, set `evaluation.max_repair_rounds` (or
`--max-repair-rounds`) above zero. Each of the `evaluation.attempts` samples remains an independent
trajectory with at most `1 + max_repair_rounds` generations. After a Lean proof failure, the
toolkit appends the assistant's raw response and a `tool` message containing
`Lean compiler feedback:\n\n<diagnostics>`, then sends the full history for the next generation.
Set `evaluation.repair_feedback_role: user` (or `--repair-feedback-role user`) for backends that
reject tool messages without structured tool calls. The v3 template expects
`### Revised Proof Plan` on repair turns. Successful proofs stop immediately; format and service
errors stop the trajectory, while transport retries remain separate from repair rounds. The
default repair budget is zero, preserving one-shot behavior.

The WuProver config starts with `model.max_tokens: 8192`. In the sampled Stage 3 run, the longest
passing response used 4,477 completion tokens and the longest extractable response used 6,635;
responses that failed extraction all reached the previous 30,000-token limit. Set
`MODEL_MAX_TOKENS` explicitly if another config or environment override is in use. Increasing the
limit does not resolve repetitive generations; check the serving chat template and thinking mode
when responses repeatedly run to the limit.

A custom test-template YAML maps named placeholders to normalized JSONL fields:

```yaml
schema_version: 1
id: my-lean-format
fields:
  statement:
    source: formal_statement
    transforms: [rstrip]
  hint:
    source: metadata.hint
    required: false
    default: ""
messages:
  - role: system
    content: "Return a complete Lean proof."
  - role: user
    content: "${hint}\n${statement}"
output:
  primary:
    type: last_fenced_code
    languages: [lean4]
  fallbacks:
    - type: last_fenced_code
      languages: [lean, ""]
    - type: raw_lean
```

Canonical `LeanProblem` fields such as `formal_statement`, `informal_statement`, `id`, and
`environment` can be mapped directly. Unrecognized top-level fields from an imported JSONL row
are preserved under `metadata`, so a source key such as `hint` is mapped as `metadata.hint`.
Supported transforms are `strip`, `rstrip`, `lean_comment`, and `json`. Fallback extractors run in
declaration order only after the primary extractor fails; the selected strategy and whether a
fallback was used are stored in each result row.

Keep only secrets and deployment identity in `.env`. For the default local vLLM-compatible
endpoint:

```dotenv
MODEL_NAME=Qwen/Qwen3-8B
MODEL_API_KEY=
AXLE_API_KEY=
```

Deployment-specific environment variables can still override YAML values. For DeepSeek:

```dotenv
MODEL_BASE_URL=https://api.deepseek.com/v1
MODEL_API_KEY=replace-me
MODEL_NAME=deepseek-chat
```

Use `LEAN_EVAL_CONFIG=/path/to/config.yaml` to select another root YAML file. Relative entries in
its `includes` list resolve next to that file. Configuration precedence is explicit CLI overrides,
process environment, `.env`, then composed YAML defaults.

The existing `MODEL_*`, `AXLE_*`, `RETRY_BACKOFF_SECONDS`, and `EVAL_*` variables remain supported
as overrides. Provider-specific JSON parameters and headers use `MODEL_EXTRA_BODY` and
`MODEL_EXTRA_HEADERS`. `MODEL_CHAT_TEMPLATE` and `EVAL_TEST_TEMPLATE` select the two independent
template layers. `AXLE_ENVIRONMENT` is only a fallback for custom tasks without version metadata.
Secrets in `.env`, evaluation output in `results/`, and temporary upstream checkouts are ignored
by Git.

`MODEL_MAX_RETRIES` and `AXLE_MAX_RETRIES` count retries after the initial request. Transient model
network errors, HTTP 429/5xx responses, and retryable AXLE server errors use exponential backoff
starting at `RETRY_BACKOFF_SECONDS`. Invalid requests and ordinary Lean verification failures are
not retried.

## Running evaluations

Start with one miniF2F problem:

```bash
uv run lean-eval run data/minif2f/problems.jsonl \
  --name minif2f --split test --limit 1
```

Use `--split test` for miniF2F evaluation. Its 242 test statements are AXLE-verified with
`import Mathlib` under `lean-4.30.0`; the validation split remains explicitly unverified.

Run PutnamBench with four attempts per problem:

```bash
uv run lean-eval run data/putnambench/problems.jsonl \
  --name putnambench --attempts 4 --concurrency 4
```

Select exact problem IDs by repeating `--id`:

```bash
uv run lean-eval run data/putnambench/problems.jsonl \
  --name putnambench --id putnam_1968_a1
```

Force an AXLE Lean environment for a compatibility experiment:

```bash
uv run lean-eval run data/minif2f/problems.jsonl \
  --name minif2f --environment lean-4.28.0
```

The precedence is `--environment` > the dataset record's `environment` > the
`AXLE_ENVIRONMENT` fallback. The override applies only to the current run, does not modify the
versioned JSONL snapshot, and is recorded in `run.json`.

The default test template uses the same message format as the project's SFT data: one `user`
message beginning
with `Complete the following Lean 4 code:`, followed by the source in a `lean4` fence and the
detailed-proof-plan instruction. It first extracts the exact `### Complete Lean 4 Proof` section
used by training. Configured fallbacks then accept a relaxed section, the final `lean4`/`lean` or
unlabelled fence, and finally a response that is recognizable as raw Lean. This tolerance affects
extraction only; AXLE still decides whether the resulting proof is valid.

Source copyright, release, license, and author headers are stored as provenance in
`metadata.source_header`; they are removed from `formal_statement` and are never sent to the model.

Each run creates:

```text
results/<UTC timestamp>-<model>/
├── run.json
├── results.jsonl
└── summary.json
```

Completed attempts are appended immediately, so useful results survive an interrupted run. API
keys are never written to these artifacts.

Each `results.jsonl` row is one independent attempt/trajectory. Its ordered `rounds` array stores
`round` (zero-based), `raw_response`, `finish_reason`, extracted `candidate`, `extraction_strategy`,
`used_extraction_fallback`, `verification` (including Lean diagnostics), `feedback` sent to the
next round, `usage`,
`generation_ms`, `verification_ms`, and any `error_stage`/`error`. The attempt-level candidate,
pass status, usage, and timing fields remain for compatibility; attempt timing is cumulative across
rounds, while attempt usage describes the final generated response. A trajectory stops at its first
passing round. Format or service errors stop it with a recorded error. `run.json` records the test
template ID, repair budget, feedback role, model serving settings, chat template, and AXLE settings
without API keys.

`summary.json` keeps `pass_at_k` as an alias of `final_pass_at_k`. `direct_pass_at_1` and
`direct_pass_at_k` use only round 0; `final_pass_at_1` and `final_pass_at_k` count any successful
round. `@1` uses attempt 1 for each problem; `@k` uses any of the configured independent attempts.
Each pass rate divides solved problems by the number of problems. The summary also reports
`problems_solved_before_repair`, `additional_problems_solved_after_repair`, average/maximum repair
rounds entered per trajectory, and `success_by_round`. Each round entry counts trajectories that
reached that round and those first solved there; its success, strict-format, and fallback rates
divide by trajectories reached. Failed generations remain in that denominator. Round entries sum
generation/verification milliseconds and prompt/completion tokens; summary totals aggregate all
trajectories, with average latency per trajectory. With `max_repair_rounds=0`, direct and final
metrics coincide and the existing pass@k interpretation is unchanged.

## Adding or updating datasets

List supported layouts and normalize a custom source:

```bash
uv run lean-eval datasets list
uv run lean-eval datasets import path/to/tasks \
  --name custom --output data/custom/problems.jsonl
```

The normalized JSONL format requires `id` and a `formal_statement` containing at least one `sorry`.
An explicit AXLE environment is strongly recommended:

```json
{"id":"demo","dataset":"custom","split":"dev","environment":"lean-4.27.0","formal_statement":"import Mathlib\ntheorem demo : True := by\n  sorry\n","metadata":{}}
```

When updating a bundled snapshot, also update its `PROVENANCE.md` with the upstream commit, task
counts, extraction date, transformation notes, and SHA-256 digest.

## Verification rule

The untouched benchmark source is passed to AXLE as `formal_statement`; the extracted candidate,
with the benchmark preamble added when needed, is passed as `content`, with
`permitted_sorries=[]`. A result passes only when AXLE returns
`okay = true` and an empty `failed_declarations` list. Candidate Lean code is sent to the model API
and AXLE but is never executed locally.

Candidates should contain the completed target declaration only. The benchmark
`formal_statement` supplies imports, options, namespaces, and the proof hole; repeating that
preamble in the model response is unnecessary. For declaration-only candidates, the verifier
prepends the benchmark preamble to AXLE `content`, so scoped notation such as `Nat` factorial
parses in the same context; the original extracted candidate remains unchanged in results.
`verification.applied_preamble` records the inserted text. A changed, recognizable named theorem
statement is flagged in `verification.candidate_statement_changed`; if AXLE rejects it, repair
feedback asks the model
to restore the original declaration. AXLE remains the authority for pass/fail, including
definitionally equivalent statements. To check this contract against a live AXLE service, set
`AXLE_API_KEY` (or configure it in `.env`) and run
`AXLE_INTEGRATION=1 uv run pytest tests/test_axle_integration.py`. The checked fixture covers a
declaration-only success and rejection of a changed signature, `sorry`, a tactic without its import, and a
tactic body without a declaration.

## Development and validation record

```bash
uv run ruff check .
uv run pytest
```

The test suite covers unit logic, CLI behavior, API contracts, dataset integrity, and snapshot
digests. A live smoke test using the configured DeepSeek endpoint successfully generated a
miniF2F proof that passed AXLE under the dataset-pinned `lean-4.27.0` environment. PutnamBench
environment selection was separately confirmed against AXLE, including strict rejection of an
unfilled `sorry` declaration.
