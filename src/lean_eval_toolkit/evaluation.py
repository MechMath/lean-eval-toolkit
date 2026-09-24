"""Concurrent, reproducible generation and verification pipeline."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from lean_eval_toolkit.config import Settings
from lean_eval_toolkit.datasets import LeanProblem
from lean_eval_toolkit.model import Generation
from lean_eval_toolkit.verifier import Verification


class Generator(Protocol):
    async def generate(self, problem: LeanProblem) -> Generation: ...


class Verifier(Protocol):
    async def verify(self, problem: LeanProblem, candidate: str) -> Verification: ...


@dataclass(slots=True)
class RoundResult:
    round: int
    raw_response: str | None = None
    candidate: str | None = None
    extraction_strategy: str | None = None
    used_extraction_fallback: bool = False
    verification: dict[str, Any] | None = None
    usage: dict[str, int] = field(default_factory=dict)
    generation_ms: int = 0
    verification_ms: int = 0
    error_stage: str | None = None
    error: str | None = None


@dataclass(slots=True)
class AttemptResult:
    problem_id: str
    dataset: str
    split: str
    attempt: int
    passed: bool = False
    candidate: str | None = None
    raw_response: str | None = None
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    extraction_strategy: str | None = None
    used_extraction_fallback: bool = False
    verification: dict[str, Any] | None = None
    error_stage: str | None = None
    error: str | None = None
    generation_ms: int = 0
    verification_ms: int = 0
    rounds: list[RoundResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvaluationSummary:
    problems: int
    attempts_per_problem: int
    total_attempts: int
    passed_attempts: int
    solved_problems: int
    pass_at_k: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _one_attempt(
    problem: LeanProblem,
    attempt: int,
    generator: Generator,
    verifier: Verifier,
    semaphore: asyncio.Semaphore,
    on_result: Callable[[AttemptResult], Awaitable[None]] | None,
    max_repair_rounds: int,
    repair_feedback_role: str,
) -> AttemptResult:
    result = AttemptResult(
        problem_id=problem.id,
        dataset=problem.dataset,
        split=problem.split,
        attempt=attempt,
    )
    async with semaphore:
        messages: list[dict[str, str]] | None = None
        for round_index in range(max_repair_rounds + 1):
            round_result = RoundResult(round=round_index)
            result.rounds.append(round_result)
            started = time.perf_counter()
            try:
                if round_index == 0:
                    generation = await generator.generate(problem)
                else:
                    assert messages is not None
                    generation = await generator.generate(
                        problem, messages=messages, repair_round=round_index
                    )
            except Exception as exc:  # noqa: BLE001 - preserve failed trajectory
                result.error_stage = "generation"
                result.error = f"{type(exc).__name__}: {exc}"
                round_result.error_stage = result.error_stage
                round_result.error = result.error
                round_result.generation_ms = round((time.perf_counter() - started) * 1000)
                result.generation_ms += round_result.generation_ms
                break
            round_result.generation_ms = round((time.perf_counter() - started) * 1000)
            result.generation_ms += round_result.generation_ms
            result.candidate = generation.content
            result.raw_response = generation.raw_content
            round_result.candidate = generation.content
            round_result.raw_response = generation.raw_content
            result.finish_reason = generation.finish_reason
            result.usage = generation.usage
            round_result.usage = generation.usage
            result.extraction_strategy = generation.extraction_strategy
            result.used_extraction_fallback = generation.used_extraction_fallback
            round_result.extraction_strategy = generation.extraction_strategy
            round_result.used_extraction_fallback = generation.used_extraction_fallback
            started = time.perf_counter()
            try:
                verification = await verifier.verify(problem, generation.content)
            except Exception as exc:  # noqa: BLE001 - preserve external service failures
                result.error_stage = "verification"
                result.error = f"{type(exc).__name__}: {exc}"
                round_result.error_stage = result.error_stage
                round_result.error = result.error
                round_result.verification_ms = round((time.perf_counter() - started) * 1000)
                result.verification_ms += round_result.verification_ms
                break
            else:
                result.verification = verification.to_dict()
                result.passed = verification.passed
                round_result.verification = result.verification
            round_result.verification_ms = round((time.perf_counter() - started) * 1000)
            result.verification_ms += round_result.verification_ms
            if result.passed or round_index == max_repair_rounds:
                break
            if messages is None:
                template = getattr(generator, "test_template", None)
                if template is None:
                    raise ValueError("repair requires a generator with a test_template")
                messages = template.render(problem)
            diagnostics = [
                *verification.lean_errors,
                *verification.failed_declarations,
                *verification.tool_errors,
            ]
            feedback = "\n".join(diagnostics) if diagnostics else "Lean verification failed."
            messages = [
                *messages,
                {"role": "assistant", "content": generation.raw_content},
                {
                    "role": repair_feedback_role,
                    "content": f"Lean compiler feedback:\n\n{feedback}",
                },
            ]
    if on_result:
        await on_result(result)
    return result


async def evaluate(
    problems: Sequence[LeanProblem],
    generator: Generator,
    verifier: Verifier,
    *,
    attempts: int,
    concurrency: int,
    max_repair_rounds: int = 0,
    repair_feedback_role: str = "tool",
    on_result: Callable[[AttemptResult], Awaitable[None]] | None = None,
) -> tuple[list[AttemptResult], EvaluationSummary]:
    """Generate and verify all attempts without failing the whole run on one error."""
    if attempts < 1 or concurrency < 1 or max_repair_rounds < 0:
        raise ValueError("attempts and concurrency must be positive; repair rounds nonnegative")
    if repair_feedback_role not in {"tool", "user"}:
        raise ValueError("repair_feedback_role must be tool or user")
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        _one_attempt(
            problem, attempt, generator, verifier, semaphore, on_result,
            max_repair_rounds, repair_feedback_role,
        )
        for problem in problems
        for attempt in range(1, attempts + 1)
    ]
    results = list(await asyncio.gather(*tasks))
    solved = {
        (result.dataset, result.split, result.problem_id) for result in results if result.passed
    }
    summary = EvaluationSummary(
        problems=len(problems),
        attempts_per_problem=attempts,
        total_attempts=len(results),
        passed_attempts=sum(result.passed for result in results),
        solved_problems=len(solved),
        pass_at_k=len(solved) / len(problems) if problems else 0.0,
    )
    return results, summary


class RunWriter:
    """Stream results to disk so interrupted evaluations retain completed attempts."""

    def __init__(
        self,
        settings: Settings,
        *,
        dataset_source: Path,
        problems: Sequence[LeanProblem],
        environment_override: str | None = None,
    ):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        model_slug = (settings.model.name or "unknown-model").replace("/", "-")
        self.directory = settings.evaluation.results_dir / f"{stamp}-{model_slug}"
        self.directory.mkdir(parents=True, exist_ok=False)
        self.results_path = self.directory / "results.jsonl"
        self._lock = asyncio.Lock()
        manifest = {
            "created_at": datetime.now(UTC).isoformat(),
            "dataset_source": str(dataset_source),
            "model_name": settings.model.name,
            "model_base_url": settings.model.base_url,
            "axle_api_url": settings.axle.api_url,
            "axle_environment_fallback": settings.axle.environment,
            "environment_override": environment_override,
            "dataset_environments": sorted(
                {
                    environment
                    for problem in problems
                    if (environment := problem.environment or settings.axle.environment)
                }
            ),
            "attempts": settings.evaluation.attempts,
            "concurrency": settings.evaluation.concurrency,
            "test_template": settings.evaluation.test_template,
            "model_chat_template_configured": settings.model.chat_template is not None,
        }
        (self.directory / "run.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    async def append(self, result: AttemptResult) -> None:
        line = json.dumps(result.to_dict(), ensure_ascii=False) + "\n"
        async with self._lock:
            with self.results_path.open("a", encoding="utf-8") as output:
                output.write(line)

    def write_summary(self, summary: EvaluationSummary) -> None:
        (self.directory / "summary.json").write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
