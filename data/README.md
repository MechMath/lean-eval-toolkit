# Dataset snapshots

This directory contains the theorem statements used by this toolkit, normalized as JSONL for
reproducible evaluation. Each subdirectory includes provenance, transformation notes, the exact
upstream commit, a SHA-256 digest, and a copy of the applicable upstream license.

The snapshots contain open benchmark statements with `sorry` placeholders, not generated model
answers or reference proofs. Do not edit `problems.jsonl` by hand; update the upstream checkout,
run the documented import command, review the diff, and update its provenance digest.

- `minif2f/problems.jsonl`: 498 tasks, split into 256 validation and 242 test tasks.
- `putnambench/problems.jsonl`: 672 test tasks.

All records explicitly pin `environment` to `lean-4.27.0`. The runtime uses this per-record value
instead of the `.env` fallback.
