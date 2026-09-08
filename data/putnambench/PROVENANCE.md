# PutnamBench snapshot provenance

- Upstream authors: George Tsoukalas et al. and PutnamBench contributors
- Upstream repository: https://github.com/trishullab/PutnamBench
- Upstream commit: `b3e08943b1728842194fe2df693f02c763da4294`
- Extracted: 2026-09-08
- Upstream Lean toolchain: `leanprover/lean4:v4.27.0`
- AXLE environment stored in every record: `lean-4.27.0`
- Output SHA-256: `c77b8f54caa9f0d882292cf508e7a4d26bfbb5248ee9a262c7827ae94b9d1e84`
- License: Apache-2.0; see `LICENSE.upstream`

## Transformation performed by Lean Eval Toolkit

We read all 672 upstream files under `lean4/src/*.lean`. Each complete source file became one JSONL
record. The record ID is the original filename without `.lean`, the split is `test`, and metadata
records the upstream source path and toolchain source. Existing comments, informal docstrings,
answer abbreviations, imports, declarations, and `sorry` placeholders remain in the complete
`formal_statement` string.

We normalized only the container format and added dataset, split, environment, and provenance
metadata. We did not synthesize proofs, alter theorem types, or include reference solutions.

Regeneration command (with an upstream checkout at the recorded commit):

```bash
uv run lean-eval datasets import <PutnamBench-checkout> \
  --name putnambench --output data/putnambench/problems.jsonl
```
