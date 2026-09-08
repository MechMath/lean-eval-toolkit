from pathlib import Path

import pytest
from typer.testing import CliRunner

from lean_eval_toolkit.cli import app


def test_run_command_wires_dataset_and_cli_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "task.lean"
    source.write_text("theorem task : True := by sorry\n", encoding="utf-8")
    output = tmp_path / "result"
    captured: dict[str, object] = {}

    async def fake_run(problems, settings, dataset_source, progress, progress_task):
        captured["problems"] = problems
        captured["settings"] = settings
        captured["source"] = dataset_source
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
            "--concurrency",
            "1",
            "--results-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Solved 1/1" in result.stdout
    assert captured["source"] == source
    [problem] = captured["problems"]
    assert problem.dataset == "manual"
    assert captured["settings"].eval_attempts == 2


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
