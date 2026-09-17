# PutnamBench snapshot provenance

- Upstream authors: George Tsoukalas et al. and PutnamBench contributors
- Upstream repository: https://github.com/trishullab/PutnamBench
- Upstream commit: `b3e08943b1728842194fe2df693f02c763da4294`
- Extracted: 2026-09-08
- Upstream Lean toolchain: `leanprover/lean4:v4.27.0`
- AXLE environment stored in every record: `lean-4.30.0` (verified)
- Output SHA-256: `9dda6adc70ce209ef09ce4dbebfa5706d72286510afd1ae753943a76d04e34b7`
- License: Apache-2.0; see `LICENSE.upstream`

## Transformation performed by Lean Eval Toolkit

We read all 672 upstream files under `lean4/src/*.lean`. Each complete source file became one JSONL
record. The record ID is the original filename without `.lean`, the split is `test`, and metadata
records the upstream source path and toolchain source. Existing comments, informal docstrings,
answer abbreviations, imports, declarations, and `sorry` placeholders remain in the complete
`formal_statement` string.

We normalized the container format and added dataset, split, environment, and provenance metadata.
All records use `import Mathlib`. One statement-preserving Lean 4.30 compatibility rewrite was
applied to `putnam_1966_b5`: the removed `s.toSet` projection was replaced by the equivalent set
coercion `(s : Set (EuclideanSpace ℝ (Fin 2)))`. The rewrite is recorded in that record's
`metadata.compatibility_transformations`. We did not synthesize proofs, alter theorem types, or
include reference solutions.

On 2026-09-17, all 672 statements were checked remotely with AXLE `lean-4.30.0`, using their
existing `sorry` declarations and permitting those declarations only for this compatibility run.
After the one compatibility rewrite, every statement passed without Lean or tool errors. Each
record stores the result in `metadata.axle_compatibility`.

Regeneration command (with an upstream checkout at the recorded commit):

```bash
uv run lean-eval datasets import <PutnamBench-checkout> \
  --name putnambench --output data/putnambench/problems.jsonl
```
