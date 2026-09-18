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
`MODEL_EXTRA_HEADERS`. `AXLE_ENVIRONMENT` is only a fallback for custom tasks without version
metadata. Secrets in `.env`, evaluation output in `results/`, and temporary upstream checkouts are
ignored by Git.

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

Model requests use the same chat format as the project's SFT data: one `user` message beginning
with `Complete the following Lean 4 code:`, followed by the source in a `lean4` fence and the
detailed-proof-plan instruction. When a benchmark provides an informal statement (for example,
miniF2F), it is prepended inside that fence as Lean `--` comments. Model responses may include the
requested reasoning, but must end with an explicitly labelled `lean4` fenced block; that final
block is the candidate sent to AXLE.

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

The untouched benchmark source is passed to AXLE as `formal_statement`; the model candidate is
passed as `content`, with `permitted_sorries=[]`. A result passes only when AXLE returns
`okay = true` and an empty `failed_declarations` list. Candidate Lean code is sent to the model API
and AXLE but is never executed locally.

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
