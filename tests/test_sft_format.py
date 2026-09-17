import pytest

from lean_eval_toolkit.sft_format import (
    SFTFormatError,
    build_sft_messages,
    build_sft_prompt,
    extract_sft_lean_code,
    split_source_header,
)


def test_builds_exact_sft_conversation_shape() -> None:
    source = "import Mathlib\ntheorem demo : True := by sorry\n"

    prompt = build_sft_prompt(source)

    assert prompt == (
        "Complete the following Lean 4 code:\n\n"
        "```lean4\n"
        "import Mathlib\n"
        "theorem demo : True := by sorry```\n\n"
        "Before producing the Lean 4 code to formally prove the given theorem, provide a detailed "
        "proof plan outlining the main proof steps and strategies.\n"
        "The plan should highlight key ideas, intermediate lemmas, and proof structures that will "
        "guide the construction of the final formal proof."
    )
    assert build_sft_messages(source) == [{"role": "user", "content": prompt}]


def test_inserts_multiline_informal_statement_as_safe_lean_comments() -> None:
    source = "theorem demo : True := by sorry"

    prompt = build_sft_prompt(source, "First line.\n\nSecond line with -/ text.")

    assert (
        "```lean4\n"
        "-- Informal statement:\n"
        "-- First line.\n"
        "--\n"
        "-- Second line with -/ text.\n\n"
        "theorem demo : True := by sorry```"
    ) in prompt


def test_removes_source_header_from_model_prompt() -> None:
    source_header = """/-
Copyright (c) Example Authors.
Released under Apache 2.0 license.
Authors: Example Author
-/"""
    source = f"{source_header}\nimport Mathlib\ntheorem demo : True := by sorry\n"

    cleaned, extracted = split_source_header(source)
    prompt = build_sft_prompt(source)

    assert extracted == source_header
    assert cleaned.startswith("import Mathlib")
    assert "Copyright" not in prompt
    assert "Released under" not in prompt
    assert "Authors:" not in prompt
    assert "```lean4\nimport Mathlib" in prompt


def test_extracts_last_lean4_block_case_insensitively() -> None:
    response = """```lean4
theorem draft : False := by sorry
```
```Lean4
theorem final : True := by trivial
```
"""

    assert extract_sft_lean_code(response) == "theorem final : True := by trivial\n"


@pytest.mark.parametrize("response", ["", "plain Lean", "```lean\n#check True\n```"])
def test_requires_explicit_lean4_block(response: str) -> None:
    with pytest.raises(SFTFormatError, match="lean4"):
        extract_sft_lean_code(response)
