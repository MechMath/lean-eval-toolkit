# miniF2F snapshot provenance

- Upstream authors: OpenAI miniF2F authors and Google DeepMind maintainers/contributors
- Upstream repository: https://github.com/google-deepmind/miniF2F
- Upstream commit: `f0a20e14c1eeccd859d51bb4c2b3ee487889c303`
- Extracted: 2026-09-08
- Upstream Lean toolchain: `leanprover/lean4:v4.27.0`
- AXLE environment stored in every record: `lean-4.27.0`
- Output SHA-256: `829203975f27262b9009f5f6f7498c4e8a694b4966b0ed13564837fb27b61532`
- License: Apache-2.0; see `LICENSE.upstream`

## Transformation performed by Lean Eval Toolkit

We read upstream `MiniF2F/Valid.lean` and `MiniF2F/Test.lean`, split the aggregate files at Lean
`theorem` declaration boundaries, and retained declarations containing a line-level `sorry`.
The shared file prelude is copied into each task. Adjacent Lean docstrings are copied into the
`informal_statement` field. Original theorem identifiers and validation/test membership are
preserved. Metadata records the upstream source file and toolchain source.

The resulting snapshot has 498 tasks: 256 validation and 242 test. Two test declarations which
already contain proofs and the support module `ProblemImports.lean` are intentionally excluded.
We did not synthesize proofs, alter theorem types, or include reference solutions.

Regeneration command (with an upstream checkout at the recorded commit):

```bash
uv run lean-eval datasets import <miniF2F-checkout> \
  --name minif2f --output data/minif2f/problems.jsonl
```
