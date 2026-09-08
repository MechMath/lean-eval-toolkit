"""Dataset normalization for built-in and user-provided Lean benchmarks."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class DatasetError(ValueError):
    """Raised when a dataset cannot be normalized safely."""


class LeanProblem(BaseModel):
    """Portable representation consumed by the evaluation pipeline."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    split: str = "custom"
    formal_statement: str = Field(min_length=1)
    informal_statement: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("formal_statement")
    @classmethod
    def statement_must_contain_placeholder(cls, value: str) -> str:
        if "sorry" not in value:
            raise ValueError("formal_statement must contain at least one `sorry` placeholder")
        return value


BUILTIN_DATASETS: dict[str, dict[str, str]] = {
    "minif2f": {
        "description": "Olympiad-level miniF2F problems translated to Lean 4",
        "upstream": "https://github.com/google-deepmind/miniF2F",
        "layout": "JSONL export or a checkout containing MiniF2F/Valid.lean and Test.lean",
    },
    "putnambench": {
        "description": "Putnam competition problems formalized in Lean 4",
        "upstream": "https://github.com/trishullab/PutnamBench",
        "layout": "Repository checkout; Lean tasks are read from lean4/src/*.lean",
    },
}

_ID_KEYS = ("id", "name", "problem_id")
_FORMAL_KEYS = ("formal_statement", "statement", "code", "formal")
_INFORMAL_KEYS = ("informal_statement", "nl_statement", "problem", "informal")


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((record[key] for key in keys if record.get(key) is not None), None)


def _normalize_record(
    record: dict[str, Any], *, dataset: str, default_split: str, line_number: int
) -> LeanProblem:
    problem_id = _first(record, _ID_KEYS)
    formal = _first(record, _FORMAL_KEYS)
    if not isinstance(problem_id, str) or not isinstance(formal, str):
        raise DatasetError(
            f"record {line_number} needs string fields `id` and `formal_statement` "
            f"(accepted aliases: {_ID_KEYS} and {_FORMAL_KEYS})"
        )
    known = {*_ID_KEYS, *_FORMAL_KEYS, *_INFORMAL_KEYS, "dataset", "split", "metadata"}
    metadata = dict(record.get("metadata") or {})
    metadata.update({key: value for key, value in record.items() if key not in known})
    try:
        return LeanProblem(
            id=problem_id,
            dataset=str(record.get("dataset") or dataset),
            split=str(record.get("split") or default_split),
            formal_statement=formal,
            informal_statement=_first(record, _INFORMAL_KEYS),
            metadata=metadata,
        )
    except ValidationError as exc:
        raise DatasetError(f"invalid record {line_number}: {exc}") from exc


def load_jsonl(path: Path, *, dataset: str, split: str = "custom") -> Iterator[LeanProblem]:
    """Load common benchmark JSONL variants into :class:`LeanProblem`."""
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"invalid JSON at {path}:{line_number}: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise DatasetError(f"record at {path}:{line_number} must be a JSON object")
            yield _normalize_record(
                record, dataset=dataset, default_split=split, line_number=line_number
            )


def _infer_split(path: Path) -> str:
    lowered = "/".join(path.parts).lower()
    if "valid" in lowered:
        return "validation"
    if "test" in lowered:
        return "test"
    return "custom"


def _lean_files(source: Path, dataset: str) -> list[Path]:
    if source.is_file():
        return [source]
    if dataset == "putnambench" and (source / "lean4" / "src").is_dir():
        source = source / "lean4" / "src"
    return sorted(source.rglob("*.lean"))


def load_lean_files(
    source: Path, *, dataset: str, split: str | None = None
) -> Iterator[LeanProblem]:
    """Treat each Lean file as one problem, preserving the complete source for AXLE."""
    files = _lean_files(source, dataset)
    if not files:
        raise DatasetError(f"no .lean files found under {source}")
    base = source if source.is_dir() else source.parent
    for path in files:
        content = path.read_text(encoding="utf-8")
        if "sorry" not in content:
            continue
        relative = path.relative_to(base)
        yield LeanProblem(
            id=relative.with_suffix("").as_posix(),
            dataset=dataset,
            split=split or _infer_split(relative),
            formal_statement=content,
            metadata={"source_path": relative.as_posix()},
        )


def load_dataset(
    source: Path, *, dataset: str = "custom", split: str | None = None
) -> list[LeanProblem]:
    """Load a JSONL file, Lean file, or directory of Lean files."""
    if not source.exists():
        raise DatasetError(f"dataset source does not exist: {source}")
    if source.is_file() and source.suffix.lower() in {".jsonl", ".json"}:
        problems = list(load_jsonl(source, dataset=dataset, split=split or "custom"))
    else:
        problems = list(load_lean_files(source, dataset=dataset, split=split))
    if not problems:
        raise DatasetError(f"no problems containing `sorry` found in {source}")
    ids = [problem.id for problem in problems]
    duplicates = sorted({problem_id for problem_id in ids if ids.count(problem_id) > 1})
    if duplicates:
        raise DatasetError(f"duplicate problem ids: {', '.join(duplicates[:5])}")
    return problems


def write_jsonl(problems: Iterable[LeanProblem], destination: Path) -> int:
    """Write normalized tasks atomically enough to avoid partial line records."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with destination.open("w", encoding="utf-8") as output:
        for problem in problems:
            output.write(problem.model_dump_json(exclude_none=True) + "\n")
            count += 1
    return count
