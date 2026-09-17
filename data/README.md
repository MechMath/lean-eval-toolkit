# Dataset snapshots

This directory contains the theorem statements used by this toolkit, normalized as JSONL for
reproducible evaluation. Each subdirectory includes provenance, transformation notes, the exact
upstream commit, a SHA-256 digest, and a copy of the applicable upstream license.

The snapshots contain open benchmark statements with `sorry` placeholders, not generated model
answers or reference proofs. Do not edit `problems.jsonl` by hand; update the upstream checkout,
run the documented import command, review the diff, and update its provenance digest.

- `minif2f/problems.jsonl`: 498 tasks, split into 256 unverified validation tasks and 242
  AXLE-verified test tasks.
- `putnambench/problems.jsonl`: 672 AXLE-verified test tasks.

All records explicitly pin an `environment`. miniF2F test records use AXLE `lean-4.30.0`;
PutnamBench records also use AXLE `lean-4.30.0`; miniF2F validation records currently retain
`lean-4.27.0`. The runtime uses this per-record value instead of the `.env` fallback.

Leading copyright, release, license, and author blocks are kept in
`metadata.source_header` rather than `formal_statement`. This preserves source attribution in the
snapshot without including it in prompts sent to evaluated models.
