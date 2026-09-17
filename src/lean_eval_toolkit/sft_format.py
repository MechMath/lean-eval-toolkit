"""Prompt formatting and response parsing for the model's SFT chat format."""

from __future__ import annotations

import re

SFT_PROMPT_PREFIX = "Complete the following Lean 4 code:\n\n```lean4\n"
SFT_PROMPT_SUFFIX = (
    "```\n\nBefore producing the Lean 4 code to formally prove the given theorem, provide a "
    "detailed proof plan outlining the main proof steps and strategies.\n"
    "The plan should highlight key ideas, intermediate lemmas, and proof structures that will "
    "guide the construction of the final formal proof."
)

_LEAN4_FENCE = re.compile(
    r"^[ \t]*```lean4[ \t]*\r?\n(?P<code>.*?)(?:\r?\n)?^[ \t]*```[ \t]*$",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)
_LEADING_BLOCK_COMMENT = re.compile(
    r"\A\ufeff?[ \t]*(?P<header>/-.*?-/)[ \t]*(?:\r?\n)+",
    re.DOTALL,
)
_SOURCE_HEADER_MARKERS = ("copyright", "released under", "authors:", "license")


class SFTFormatError(ValueError):
    """Raised when model data does not follow the expected SFT format."""


def split_source_header(source: str) -> tuple[str, str | None]:
    """Remove a leading copyright/authorship block while returning it for provenance."""
    match = _LEADING_BLOCK_COMMENT.match(source)
    if not match:
        return source, None
    header = match.group("header")
    lowered = header.lower()
    if not any(marker in lowered for marker in _SOURCE_HEADER_MARKERS):
        return source, None
    return source[match.end() :], header


def _informal_comment(informal_statement: str) -> str:
    """Render informal prose as line comments that cannot terminate a Lean comment early."""
    lines = informal_statement.strip().splitlines()
    rendered = ["-- Informal statement:"]
    rendered.extend(f"-- {line}" if line else "--" for line in lines)
    return "\n".join(rendered)


def build_sft_prompt(formal_statement: str, informal_statement: str | None = None) -> str:
    """Wrap Lean source and optional informal prose in the SFT user prompt."""
    source, _ = split_source_header(formal_statement)
    source = source.rstrip()
    if not source:
        raise SFTFormatError("formal statement is empty")
    if informal_statement and informal_statement.strip():
        source = f"{_informal_comment(informal_statement)}\n\n{source}"
    return f"{SFT_PROMPT_PREFIX}{source}{SFT_PROMPT_SUFFIX}"


def build_sft_messages(
    formal_statement: str, informal_statement: str | None = None
) -> list[dict[str, str]]:
    """Build the single-user-message conversation used during fine-tuning."""
    return [
        {
            "role": "user",
            "content": build_sft_prompt(formal_statement, informal_statement),
        }
    ]


def extract_sft_lean_code(response: str) -> str:
    """Extract the final explicitly labelled ``lean4`` block from a model response."""
    matches = list(_LEAN4_FENCE.finditer(response))
    if not matches:
        raise SFTFormatError("model response does not contain a fenced `lean4` code block")
    code = matches[-1].group("code").strip()
    if not code:
        raise SFTFormatError("model returned an empty `lean4` code block")
    return code + "\n"
