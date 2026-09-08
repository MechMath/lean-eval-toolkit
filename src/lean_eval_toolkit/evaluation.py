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
    verification: dict[str, Any] | None = None
    error_stage: str | None = None
    error: str | None = None
    generation_ms: int = 0
    verification_ms: int = 0

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
) -> AttemptResult:
    result = AttemptResult(
        problem_id=problem.id,
        dataset=problem.dataset,
        split=problem.split,
        attempt=attempt,
    )
    async with semaphore:
        started = time.perf_counter()
        try:
            generation = await generator.generate(problem)
        except Exception as exc:  # noqa: BLE001 - one failed task must not abort a benchmark
            result.error_stage = "generation"
            result.error = f"{type(exc).__name__}: {exc}"
            result.generation_ms = round((time.perf_counter() - started) * 1000)
        else:
            result.generation_ms = round((time.perf_counter() - started) * 1000)
            result.candidate = generation.content
            result.raw_response = generation.raw_content
            result.finish_reason = generation.finish_reason
            result.usage = generation.usage
            started = time.perf_counter()
            try:
                verification = await verifier.verify(problem, generation.content)
            except Exception as exc:  # noqa: BLE001 - preserve external service failures
                result.error_stage = "verification"
                result.error = f"{type(exc).__name__}: {exc}"
            else:
                result.verification = verification.to_dict()
                result.passed = verification.passed
            result.verification_ms = round((time.perf_counter() - started) * 1000)
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
    on_result: Callable[[AttemptResult], Awaitable[None]] | None = None,
) -> tuple[list[AttemptResult], EvaluationSummary]:
    """Generate and verify all attempts without failing the whole run on one error."""
    if attempts < 1 or concurrency < 1:
        raise ValueError("attempts and concurrency must be positive")
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        _one_attempt(problem, attempt, generator, verifier, semaphore, on_result)
        for problem in problems
        for attempt in range(1, attempts + 1)
    ]
    results = list(await asyncio.gather(*tasks))
    solved = {result.problem_id for result in results if result.passed}
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

    def __init__(self, settings: Settings, *, dataset_source: Path):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        model_slug = (settings.model_name or "unknown-model").replace("/", "-")
        self.directory = settings.eval_results_dir / f"{stamp}-{model_slug}"
        self.directory.mkdir(parents=True, exist_ok=False)
        self.results_path = self.directory / "results.jsonl"
        self._lock = asyncio.Lock()
        manifest = {
            "created_at": datetime.now(UTC).isoformat(),
            "dataset_source": str(dataset_source),
            "model_name": settings.model_name,
            "model_base_url": settings.model_base_url,
            "axle_api_url": settings.axle_api_url,
            "axle_environment": settings.axle_environment,
            "attempts": settings.eval_attempts,
            "concurrency": settings.eval_concurrency,
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
