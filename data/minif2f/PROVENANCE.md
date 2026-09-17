# miniF2F snapshot provenance

- Upstream authors: OpenAI miniF2F authors and Google DeepMind maintainers/contributors
- Upstream repository: https://github.com/google-deepmind/miniF2F
- Upstream commit: `f0a20e14c1eeccd859d51bb4c2b3ee487889c303`
- Extracted: 2026-09-08
- Upstream Lean toolchain: `leanprover/lean4:v4.27.0`
- AXLE environment for test records: `lean-4.30.0` (verified)
- Environment retained for validation records: `lean-4.27.0` (not yet verified)
- Output SHA-256: `e2ff75eed3893edf955aa77d54bdf3d0cee4e839212164fdbae4677ba07aa822`
- License: Apache-2.0; see `LICENSE.upstream`

## Transformation performed by Lean Eval Toolkit

We read upstream `MiniF2F/Valid.lean` and `MiniF2F/Test.lean`, split the aggregate files at Lean
`theorem` declaration boundaries, and retained declarations containing a line-level `sorry`.
The shared file prelude is copied into each task. Adjacent Lean docstrings are copied into the
`informal_statement` field. Original theorem identifiers and validation/test membership are
preserved. The leading copyright, release, and author block is excluded from `formal_statement`
so it is not sent to evaluated models, and is retained verbatim in `metadata.source_header` for
provenance. Metadata also records the upstream source file and toolchain source.

The 242 test records replace `import MiniF2F.ProblemImports` with `import Mathlib` and pin
`lean-4.30.0`. On 2026-09-17, all 242 statements were checked remotely with AXLE using `sorry`
permitted only for the target declaration; all passed without Lean or tool errors. Their metadata
records this check in `axle_compatibility`.

The 256 validation records have not been included in that compatibility run. They retain the
upstream import and `lean-4.27.0`, and their metadata marks them `unverified`. In particular, 204
validation statements contain `answer(...)` syntax that must be normalized before switching them
to plain `Mathlib` and AXLE `lean-4.30.0`.

The resulting snapshot has 498 tasks: 256 validation and 242 test. Two test declarations which
already contain proofs and the support module `ProblemImports.lean` are intentionally excluded.
We did not synthesize proofs, alter theorem types, or include reference solutions.

Regeneration command (with an upstream checkout at the recorded commit):

```bash
uv run lean-eval datasets import <miniF2F-checkout> \
  --name minif2f --output data/minif2f/problems.jsonl
```
