import asyncio
import json
from pathlib import Path

import pytest

from lean_eval_toolkit.config import load_settings
from lean_eval_toolkit.datasets import LeanProblem
from lean_eval_toolkit.evaluation import RunWriter, evaluate
from lean_eval_toolkit.model import Generation, GenerationFormatError
from lean_eval_toolkit.test_templates import load_test_template
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
@pytest.mark.parametrize("feedback_role", ["tool", "user"])
async def test_repair_keeps_full_history_and_stops_on_success(feedback_role: str) -> None:
    problem = problems()[0]
    failed_raw = (
        "### Proof Plan\ntry simp\n### Lean Proof\n"
        "```lean4\ntheorem one : True := by fail\n```"
    )
    passed_raw = (
        "### Revised Proof Plan\nuse trivial\n### Lean Proof\n"
        "```lean4\ntheorem one : True := by trivial\n```"
    )

    class RepairGenerator:
        test_template = load_test_template("lean-plan-repair-v3")
        calls: list[list[dict[str, str]]]

        def __init__(self) -> None:
            self.calls = []

        async def generate(
            self, task: LeanProblem, *, messages: list[dict[str, str]] | None = None,
            repair_round: int = 0,
        ) -> Generation:
            self.calls.append(messages or self.test_template.render(task))
            raw = failed_raw if repair_round == 0 else passed_raw
            extracted = self.test_template.extract(raw, repair=repair_round > 0)
            return Generation(
                content=extracted.code, raw_content=raw,
                extraction_strategy=extracted.strategy,
            )

    class RepairVerifier:
        async def verify(self, task: LeanProblem, candidate: str) -> Verification:
            return Verification(
                passed="trivial" in candidate, okay="trivial" in candidate,
                lean_errors=[] if "trivial" in candidate else ["unknown tactic 'fail'"],
            )

    generator = RepairGenerator()
    results, _ = await evaluate(
        [problem], generator, RepairVerifier(), attempts=1, concurrency=1,
        max_repair_rounds=3, repair_feedback_role=feedback_role,
    )
    assert len(generator.calls) == 2
    assert generator.calls[1] == [
        *generator.calls[0],
        {"role": "assistant", "content": failed_raw},
        {"role": feedback_role, "content": "Lean compiler feedback:\n\nunknown tactic 'fail'"},
    ]
    assert results[0].passed
    assert [item.raw_response for item in results[0].rounds] == [failed_raw, passed_raw]
    assert results[0].rounds[0].feedback == (
        "Lean compiler feedback:\n\nunknown tactic 'fail'"
    )
    assert results[0].rounds[1].extraction_strategy == "strict_revised_lean_proof"


@pytest.mark.asyncio
async def test_repair_does_not_retry_generation_or_verifier_errors() -> None:
    class FormatFailure:
        async def generate(self, task: LeanProblem) -> Generation:
            raise ValueError("response did not match template")

    class ServiceFailure:
        async def verify(self, task: LeanProblem, candidate: str) -> Verification:
            raise RuntimeError("AXLE unavailable")

    failed, _ = await evaluate(
        problems()[:1], FormatFailure(), FakeVerifier(), attempts=1, concurrency=1,
        max_repair_rounds=2,
    )
    unavailable, _ = await evaluate(
        problems()[:1], FakeGenerator(), ServiceFailure(), attempts=1, concurrency=1,
        max_repair_rounds=2,
    )
    assert len(failed[0].rounds) == len(unavailable[0].rounds) == 1
    assert failed[0].error_stage == "generation"
    assert unavailable[0].error_stage == "verification"


@pytest.mark.asyncio
async def test_repair_feedback_flags_changed_statement_after_axle_failure() -> None:
    class ChangedGenerator:
        test_template = load_test_template("lean-plan-repair-v3")
        history: list[dict[str, str]] | None = None

        async def generate(
            self, task: LeanProblem, *, messages: list[dict[str, str]] | None = None,
            repair_round: int = 0,
        ) -> Generation:
            if messages is not None:
                self.history = messages
            return Generation(content="theorem one : False := by trivial", raw_content="raw")

    class ChangedVerifier:
        async def verify(self, task: LeanProblem, candidate: str) -> Verification:
            return Verification(
                passed=False, okay=False, candidate_statement_changed=True,
                lean_errors=["type mismatch"],
            )

    generator = ChangedGenerator()
    await evaluate(
        problems()[:1], generator, ChangedVerifier(), attempts=1, concurrency=1,
        max_repair_rounds=1,
    )
    assert generator.history is not None
    assert "candidate statement differs" in generator.history[-1]["content"]
    assert "type mismatch" in generator.history[-1]["content"]


@pytest.mark.asyncio
async def test_mixed_repair_summary_and_round_usage() -> None:
    class MixedGenerator:
        test_template = load_test_template("lean-plan-repair-v3")

        def __init__(self) -> None:
            self.trajectory = {"one": 0, "two": 0}

        async def generate(
            self, task: LeanProblem, *, messages: list[dict[str, str]] | None = None,
            repair_round: int = 0,
        ) -> Generation:
            if repair_round == 0:
                self.trajectory[task.id] += 1
            attempt = self.trajectory[task.id]
            passed = (task.id, attempt, repair_round) in {
                ("one", 1, 0), ("two", 1, 1), ("two", 2, 2),
            }
            fallback = task.id == "two" and attempt == 1 and repair_round == 0
            return Generation(
                content="pass" if passed else "fail",
                raw_content=f"{task.id}/{attempt}/{repair_round}",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
                extraction_strategy="fallback" if fallback else "strict",
                used_extraction_fallback=fallback,
            )

    class MixedVerifier:
        async def verify(self, task: LeanProblem, candidate: str) -> Verification:
            return Verification(
                passed=candidate == "pass", okay=candidate == "pass",
                lean_errors=[] if candidate == "pass" else ["proof failed"],
            )

    results, summary = await evaluate(
        problems(), MixedGenerator(), MixedVerifier(), attempts=2, concurrency=1,
        max_repair_rounds=2,
    )
    assert summary.direct_pass_at_1 == summary.direct_pass_at_k == 0.5
    assert summary.final_pass_at_1 == summary.final_pass_at_k == 1.0
    assert summary.pass_at_k == 1.0
    assert summary.problems_solved_before_repair == 1
    assert summary.additional_problems_solved_after_repair == 1
    assert [item["trajectories_reached"] for item in summary.success_by_round] == [4, 3, 2]
    assert [item["success_count"] for item in summary.success_by_round] == [1, 1, 1]
    assert [item["success_rate"] for item in summary.success_by_round] == [0.25, 1 / 3, 0.5]
    assert summary.success_by_round[0]["strict_format_rate"] == 0.75
    assert summary.success_by_round[0]["extraction_fallback_rate"] == 0.25
    assert summary.average_repair_rounds_used == 1.25
    assert summary.maximum_repair_rounds_used == 2
    assert summary.total_prompt_tokens == 90
    assert summary.total_completion_tokens == 45
    assert sum(len(result.rounds) for result in results) == 9


@pytest.mark.asyncio
async def test_format_error_preserves_raw_response_and_usage() -> None:
    class BadFormat:
        async def generate(self, task: LeanProblem) -> Generation:
            raise GenerationFormatError(
                "bad format", "raw malformed", {"prompt_tokens": 7}, "length"
            )

    results, summary = await evaluate(
        problems()[:1], BadFormat(), FakeVerifier(), attempts=1, concurrency=1,
    )
    assert results[0].rounds[0].raw_response == "raw malformed"
    assert results[0].rounds[0].usage == {"prompt_tokens": 7}
    assert results[0].rounds[0].finish_reason == "length"
    assert results[0].finish_reason == "length"
    assert summary.total_prompt_tokens == 7


@pytest.mark.asyncio
async def test_run_writer_streams_results_and_summary(tmp_path: Path) -> None:
    settings = load_settings(
        env_file=None,
        overrides={
            "model": {"name": "org/model"},
            "evaluation": {"results_dir": str(tmp_path), "attempts": 1},
        },
    )
    writer = RunWriter(
        settings,
        dataset_source=Path("tasks.jsonl"),
        problems=problems()[:1],
        environment_override="lean-4.28.0",
    )
    results, summary = await evaluate(
        problems()[:1], FakeGenerator(), FakeVerifier(), attempts=1, concurrency=1,
        on_result=writer.append,
    )
    writer.write_summary(summary)

    assert results[0].passed
    assert writer.results_path.read_text(encoding="utf-8").count("\n") == 1
    assert (writer.directory / "run.json").is_file()
    assert (writer.directory / "summary.json").is_file()
    assert '"environment_override": "lean-4.28.0"' in (
        writer.directory / "run.json"
    ).read_text(encoding="utf-8")
    manifest = json.loads((writer.directory / "run.json").read_text(encoding="utf-8"))
    assert manifest["test_template_id"] == "lean-cot-v1"
    assert manifest["max_repair_rounds"] == 0
    assert manifest["repair_feedback_role"] == "tool"
    assert manifest["model_temperature"] == settings.model.temperature
    saved = json.loads(writer.results_path.read_text(encoding="utf-8"))
    assert saved["rounds"][0]["verification"]["passed"]


@pytest.mark.asyncio
async def test_interrupted_run_retains_completed_trajectories(tmp_path: Path) -> None:
    settings = load_settings(
        env_file=None, overrides={"evaluation": {"results_dir": str(tmp_path)}}
    )
    writer = RunWriter(settings, dataset_source=Path("tasks.jsonl"), problems=problems())
    completed = asyncio.Event()
    blocked = asyncio.Event()

    class SlowGenerator(FakeGenerator):
        async def generate(self, problem: LeanProblem) -> Generation:
            if problem.id == "two":
                await blocked.wait()
            return await super().generate(problem)

    async def save(result) -> None:
        await writer.append(result)
        completed.set()

    task = asyncio.create_task(
        evaluate(problems(), SlowGenerator(), FakeVerifier(), attempts=1, concurrency=1,
                 on_result=save)
    )
    await asyncio.wait_for(completed.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = writer.results_path.read_text(encoding="utf-8").splitlines()
    assert len(saved) == 1
    assert json.loads(saved[0])["problem_id"] == "one"
