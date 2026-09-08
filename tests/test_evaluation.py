from pathlib import Path

import pytest

from lean_eval_toolkit.config import Settings
from lean_eval_toolkit.datasets import LeanProblem
from lean_eval_toolkit.evaluation import RunWriter, evaluate
from lean_eval_toolkit.model import Generation
from lean_eval_toolkit.verifier import Verification


def problems() -> list[LeanProblem]:
    return [
        LeanProblem(
            id=problem_id,
            dataset="manual",
            formal_statement=f"theorem {problem_id} : True := by sorry",
        )
        for problem_id in ("one", "two")
    ]


class FakeGenerator:
    async def generate(self, problem: LeanProblem) -> Generation:
        return Generation(
            content=problem.formal_statement.replace("sorry", "trivial"),
            raw_content="raw",
            usage={"completion_tokens": 1},
        )


class FakeVerifier:
    async def verify(self, problem: LeanProblem, candidate: str) -> Verification:
        assert "trivial" in candidate
        return Verification(passed=problem.id == "one", okay=problem.id == "one")


@pytest.mark.asyncio
async def test_evaluate_computes_empirical_pass_at_k() -> None:
    results, summary = await evaluate(
        problems(), FakeGenerator(), FakeVerifier(), attempts=2, concurrency=2
    )

    assert len(results) == 4
    assert summary.passed_attempts == 2
    assert summary.solved_problems == 1
    assert summary.pass_at_k == 0.5


class BrokenGenerator:
    async def generate(self, problem: LeanProblem) -> Generation:
        raise RuntimeError(f"cannot generate {problem.id}")


@pytest.mark.asyncio
async def test_evaluate_records_generation_errors() -> None:
    results, summary = await evaluate(
        problems()[:1], BrokenGenerator(), FakeVerifier(), attempts=1, concurrency=1
    )

    assert results[0].error_stage == "generation"
    assert "cannot generate one" in (results[0].error or "")
    assert summary.pass_at_k == 0


@pytest.mark.asyncio
async def test_run_writer_streams_results_and_summary(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        model_name="org/model",
        eval_results_dir=tmp_path,
        eval_attempts=1,
    )
    writer = RunWriter(settings, dataset_source=Path("tasks.jsonl"))
    results, summary = await evaluate(
        problems()[:1], FakeGenerator(), FakeVerifier(), attempts=1, concurrency=1,
        on_result=writer.append,
    )
    writer.write_summary(summary)

    assert results[0].passed
    assert writer.results_path.read_text(encoding="utf-8").count("\n") == 1
    assert (writer.directory / "run.json").is_file()
    assert (writer.directory / "summary.json").is_file()
