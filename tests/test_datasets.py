import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lean_eval_toolkit.cli import app
from lean_eval_toolkit.datasets import DatasetError, load_dataset, write_jsonl


def test_loads_common_minif2f_jsonl_shape(tmp_path: Path) -> None:
    source = tmp_path / "valid.jsonl"
    source.write_text(
        json.dumps(
            {
                "id": "mathd_algebra_1",
                "split": "validation",
                "formal_statement": "import Mathlib\ntheorem t : True := by sorry",
                "nl_statement": "Prove that true is true.",
                "header": "import Mathlib",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    [problem] = load_dataset(source, dataset="minif2f")

    assert problem.dataset == "minif2f"
    assert problem.split == "validation"
    assert problem.informal_statement == "Prove that true is true."
    assert problem.metadata == {"header": "import Mathlib"}


def test_loads_putnambench_checkout_layout(tmp_path: Path) -> None:
    checkout = tmp_path / "PutnamBench"
    source = checkout / "lean4" / "src"
    source.mkdir(parents=True)
    (checkout / "lean4" / "lean-toolchain").write_text(
        "leanprover/lean4:v4.26.0\n", encoding="utf-8"
    )
    (source / "putnam_2024_a1.lean").write_text(
        "import Mathlib\n theorem putnam_2024_a1 : True := by sorry\n", encoding="utf-8"
    )
    (source / "README.lean").write_text("def alreadyDone := 1\n", encoding="utf-8")

    [problem] = load_dataset(tmp_path / "PutnamBench", dataset="putnambench")

    assert problem.id == "putnam_2024_a1"
    assert problem.split == "test"
    assert problem.metadata["source_path"] == "putnam_2024_a1.lean"
    assert problem.environment == "lean-4.26.0"
    assert problem.metadata["environment_source"] == "lean4/lean-toolchain"


def test_splits_upstream_minif2f_aggregate(tmp_path: Path) -> None:
    source = tmp_path / "MiniF2F"
    source.mkdir()
    (source / "Test.lean").write_text(
        """import MiniF2F.ProblemImports
open scoped Nat
/-- First problem. -/
theorem first : True := by
  sorry
/-- Second problem. -/
theorem second : 1 = 1 := by
  sorry
""",
        encoding="utf-8",
    )

    loaded = load_dataset(tmp_path, dataset="minif2f")

    assert [problem.id for problem in loaded] == ["first", "second"]
    assert all(problem.split == "test" for problem in loaded)
    assert loaded[0].informal_statement == "First problem."
    assert "theorem second" not in loaded[0].formal_statement
    assert loaded[0].environment == "lean-4.27.0"


def test_rejects_statement_without_sorry(tmp_path: Path) -> None:
    source = tmp_path / "tasks.jsonl"
    source.write_text(
        json.dumps({"id": "done", "formal_statement": "theorem done : True := by trivial"})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(DatasetError, match="sorry"):
        load_dataset(source)


def test_round_trip_normalized_jsonl(tmp_path: Path) -> None:
    lean = tmp_path / "sample.lean"
    lean.write_text("theorem sample : True := by sorry\n", encoding="utf-8")
    problems = load_dataset(lean, dataset="manual", split="dev")
    output = tmp_path / "normalized.jsonl"

    assert write_jsonl(problems, output) == 1
    [loaded] = load_dataset(output)
    assert loaded.id == "sample"
    assert loaded.dataset == "manual"
    assert loaded.split == "dev"


def test_allows_same_problem_id_in_different_splits(tmp_path: Path) -> None:
    source = tmp_path / "tasks.jsonl"
    records = [
        {"id": "same", "split": split, "formal_statement": "theorem same : True := by sorry"}
        for split in ("validation", "test")
    ]
    source.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    loaded = load_dataset(source, dataset="minif2f")

    assert len(loaded) == 2


def test_cli_lists_built_in_datasets() -> None:
    result = CliRunner().invoke(app, ["datasets", "list"])

    assert result.exit_code == 0
    assert "minif2f" in result.stdout
    assert "putnambench" in result.stdout
