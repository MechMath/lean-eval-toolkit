import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lean_eval_toolkit.cli import app


def test_run_command_wires_dataset_and_cli_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "tasks.jsonl"
    source.write_text(
        json.dumps(
            {
                "id": "task",
                "dataset": "manual",
                "environment": "lean-4.27.0",
                "formal_statement": "theorem task : True := by sorry",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "result"
    captured: dict[str, object] = {}

    async def fake_run(
        problems, settings, dataset_source, environment_override, progress, progress_task
    ):
        captured["problems"] = problems
        captured["settings"] = settings
        captured["source"] = dataset_source
        captured["environment_override"] = environment_override
        return output, 1.0, 1, 1

    monkeypatch.setattr("lean_eval_toolkit.cli._run_evaluation", fake_run)
    monkeypatch.setenv("MODEL_NAME", "test-model")
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(source),
            "--name",
            "manual",
            "--attempts",
            "2",
            "--id",
            "task",
            "--environment",
            "lean-4.28.0",
            "--concurrency",
            "1",
            "--max-truncation-retries",
            "2",
            "--results-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Solved 1/1" in result.stdout
    assert captured["source"] == source
    [problem] = captured["problems"]
    assert problem.dataset == "manual"
    assert problem.environment == "lean-4.28.0"
    assert captured["environment_override"] == "lean-4.28.0"
    assert captured["settings"].evaluation.attempts == 2
    assert captured["settings"].evaluation.max_truncation_retries == 2


def test_run_command_explains_missing_model_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "task.lean"
    source.write_text("theorem task : True := by sorry\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MODEL_NAME", raising=False)

    result = CliRunner().invoke(app, ["run", str(source)])

    assert result.exit_code == 2
    assert "MODEL_NAME is required" in result.stdout
