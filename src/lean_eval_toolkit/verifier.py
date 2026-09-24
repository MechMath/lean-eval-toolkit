"""AXLE-backed Lean proof verification."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from axle import AxleClient
from axle.exceptions import (
    AxleInternalError,
    AxleIsUnavailable,
    AxleRateLimitedError,
    AxleRuntimeError,
)

from lean_eval_toolkit.config import Settings
from lean_eval_toolkit.datasets import LeanProblem

logger = logging.getLogger(__name__)

_RETRYABLE_AXLE_ERRORS = (
    AxleIsUnavailable,
    AxleInternalError,
    AxleRateLimitedError,
    AxleRuntimeError,
)
_NAMED_DECLARATION_RE = re.compile(
    r"(?ms)^[ \t]*(?:theorem|lemma)\s+(?P<name>[A-Za-z0-9_'.]+)\b"
    r"(?P<signature>.*?)\s*:=\s*by\b"
)


def _named_declaration(source: str, name: str) -> re.Match[str] | None:
    return next(
        (match for match in _NAMED_DECLARATION_RE.finditer(source) if match["name"] == name),
        None,
    )


def _normalized_signature(match: re.Match[str]) -> str:
    return re.sub(r"\s+", "", match["signature"])


def _candidate_for_axle(problem: LeanProblem, candidate: str) -> tuple[str, str | None, bool]:
    """Supply source context to declaration-only candidates and note changed targets."""
    target = _named_declaration(problem.formal_statement, problem.id)
    if target is None:
        declarations = list(_NAMED_DECLARATION_RE.finditer(problem.formal_statement))
        if len(declarations) != 1:
            return candidate, None, False
        target = declarations[0]
    declaration = _named_declaration(candidate, target["name"])
    changed_statement = declaration is None or (
        _normalized_signature(declaration) != _normalized_signature(target)
    )
    if declaration is None or candidate[: declaration.start()].strip():
        return candidate, None, changed_statement
    preamble = problem.formal_statement[: target.start()]
    if not preamble.strip():
        return candidate, None, changed_statement
    return preamble.rstrip() + "\n\n" + candidate.lstrip(), preamble, changed_statement


@dataclass(slots=True)
class Verification:
    passed: bool
    okay: bool
    failed_declarations: list[str] = field(default_factory=list)
    lean_errors: list[str] = field(default_factory=list)
    lean_warnings: list[str] = field(default_factory=list)
    tool_errors: list[str] = field(default_factory=list)
    tool_warnings: list[str] = field(default_factory=list)
    timings: dict[str, int] = field(default_factory=dict)
    info: dict[str, Any] | None = None
    applied_preamble: str | None = None
    candidate_statement_changed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AxleVerifier:
    """Verify generated files against immutable benchmark statements using AXLE."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = AxleClient(
            url=settings.axle.api_url,
            api_key=settings.axle.api_key.get_secret_value() or None,
            max_concurrency=settings.evaluation.concurrency,
            base_timeout_seconds=settings.axle.timeout_seconds,
        )

    async def __aenter__(self) -> AxleVerifier:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.close()

    async def verify(self, problem: LeanProblem, candidate: str) -> Verification:
        environment = problem.environment or self.settings.axle.environment
        if not environment:
            raise ValueError(
                f"problem {problem.id!r} has no Lean environment; add an `environment` field, "
                "a lean-toolchain file, or AXLE_ENVIRONMENT fallback"
            )
        content, preamble, changed_statement = _candidate_for_axle(problem, candidate)
        request = {
            "formal_statement": problem.formal_statement,
            "content": content,
            "environment": environment,
            # Never permit benchmark placeholders in a successful candidate.
            "permitted_sorries": [],
            "timeout_seconds": self.settings.axle.timeout_seconds,
        }
        for retry in range(self.settings.axle.max_retries + 1):
            try:
                result = await self._client.verify_proof(**request)
                break
            except _RETRYABLE_AXLE_ERRORS as exc:
                if retry == self.settings.axle.max_retries:
                    raise
                delay = self.settings.retry.backoff_seconds * (2**retry)
                logger.warning(
                    "AXLE request failed; retrying %s/%s in %.2fs: %s",
                    retry + 1,
                    self.settings.axle.max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        else:
            raise AssertionError("unreachable")
        passed = result.okay and not result.failed_declarations
        return Verification(
            passed=passed,
            okay=result.okay,
            failed_declarations=result.failed_declarations,
            lean_errors=result.lean_messages.errors,
            lean_warnings=result.lean_messages.warnings,
            tool_errors=result.tool_messages.errors,
            tool_warnings=result.tool_messages.warnings,
            timings=result.timings,
            info=result.info,
            applied_preamble=preamble,
            candidate_statement_changed=changed_statement,
        )
