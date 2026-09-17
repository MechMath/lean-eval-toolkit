"""Dataset normalization for built-in and user-provided Lean benchmarks."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lean_eval_toolkit.sft_format import split_source_header


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
    environment: str | None = None
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
        "fallback_environment": "lean-4.27.0",
    },
    "putnambench": {
        "description": "Putnam competition problems formalized in Lean 4",
        "upstream": "https://github.com/trishullab/PutnamBench",
        "layout": "Repository checkout; Lean tasks are read from lean4/src/*.lean",
        "fallback_environment": "lean-4.30.0",
    },
}

_ID_KEYS = ("id", "name", "problem_id")
_FORMAL_KEYS = ("formal_statement", "statement", "code", "formal")
_INFORMAL_KEYS = ("informal_statement", "nl_statement", "problem", "informal")
_ENVIRONMENT_KEYS = ("environment", "lean_environment", "axle_environment")
_MINIF2F_THEOREM = re.compile(
    r"(?m)^theorem\s+(?P<name>[A-Za-z0-9_'.]+)\b"
)
_SORRY_LINE = re.compile(r"(?m)^\s+sorry\s*$")
_MINIF2F_UPSTREAM_IMPORT = "import MiniF2F.ProblemImports"
_MINIF2F_TEST_IMPORT = "import Mathlib"
_MINIF2F_TEST_ENVIRONMENT = "lean-4.30.0"
_PUTNAMBENCH_ENVIRONMENT = "lean-4.30.0"
_PUTNAM_1966_B5_OLD = "s.toSet"
_PUTNAM_1966_B5_NEW = "(s : Set (EuclideanSpace ℝ (Fin 2)))"


def _normalize_putnambench_source(problem_id: str, source: str) -> tuple[str, list[str]]:
    """Apply statement-preserving compatibility rewrites verified against AXLE."""
    transformations: list[str] = []
    if problem_id == "putnam_1966_b5" and _PUTNAM_1966_B5_OLD in source:
        source = source.replace(_PUTNAM_1966_B5_OLD, _PUTNAM_1966_B5_NEW, 1)
        transformations.append(
            f"{_PUTNAM_1966_B5_OLD} -> {_PUTNAM_1966_B5_NEW}"
        )
    return source, transformations


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
    known = {
        *_ID_KEYS,
        *_FORMAL_KEYS,
        *_INFORMAL_KEYS,
        *_ENVIRONMENT_KEYS,
        "dataset",
        "split",
        "metadata",
    }
    metadata = dict(record.get("metadata") or {})
    metadata.update({key: value for key, value in record.items() if key not in known})
    formal, source_header = split_source_header(formal)
    if source_header:
        metadata.setdefault("source_header", source_header)
    try:
        return LeanProblem(
            id=problem_id,
            dataset=str(record.get("dataset") or dataset),
            split=str(record.get("split") or default_split),
            formal_statement=formal,
            informal_statement=_first(record, _INFORMAL_KEYS),
            environment=_first(record, _ENVIRONMENT_KEYS),
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
    files = sorted(source.rglob("*.lean"))
    if dataset == "minif2f":
        aggregates = [path for path in files if path.stem.lower() in {"valid", "test"}]
        if aggregates:
            return aggregates
    return files


def _parse_toolchain(path: Path) -> str | None:
    match = re.search(r"lean4:v?(\d+\.\d+\.\d+(?:-rc\d+)?)", path.read_text(encoding="utf-8"))
    return f"lean-{match.group(1)}" if match else None


def _infer_environment(source: Path, dataset: str) -> tuple[str | None, str]:
    root = source if source.is_dir() else source.parent
    candidates = [root / "lean-toolchain"]
    if dataset == "putnambench":
        candidates.insert(0, root / "lean4" / "lean-toolchain")
    for parent in list(root.parents)[:3]:
        candidates.append(parent / "lean-toolchain")
    for candidate in candidates:
        if candidate.is_file() and (environment := _parse_toolchain(candidate)):
            try:
                source_name = candidate.relative_to(root).as_posix()
            except ValueError:
                source_name = str(candidate)
            return environment, source_name
    fallback = BUILTIN_DATASETS.get(dataset, {}).get("fallback_environment")
    return fallback, "built-in fallback" if fallback else "unspecified"


def _minif2f_problems(
    path: Path, *, base: Path, environment: str | None, environment_source: str
) -> Iterator[LeanProblem]:
    """Split the upstream aggregate Valid/Test files into independent tasks."""
    content = path.read_text(encoding="utf-8")
    matches = list(_MINIF2F_THEOREM.finditer(content))
    if len(matches) <= 1:
        return
    first_start = matches[0].start()
    prelude_end = content.rfind("/--", 0, first_start)
    prelude = content[: prelude_end if prelude_end >= 0 else first_start].rstrip()
    prelude, source_header = split_source_header(prelude)
    prelude = prelude.rstrip()
    relative = path.relative_to(base)
    split = _infer_split(relative)
    problem_environment = environment
    compatibility: dict[str, str]
    if split == "test":
        prelude = prelude.replace(_MINIF2F_UPSTREAM_IMPORT, _MINIF2F_TEST_IMPORT, 1)
        problem_environment = _MINIF2F_TEST_ENVIRONMENT
        compatibility = {
            "status": "verified",
            "checked_at": "2026-09-17",
            "environment": _MINIF2F_TEST_ENVIRONMENT,
            "import": "Mathlib",
        }
    else:
        compatibility = {
            "status": "unverified",
            "reason": (
                "not included in the AXLE test-split compatibility run; "
                "answer(...) syntax requires normalization"
            ),
        }
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        declaration = content[match.start() : next_start]
        sorry_lines = list(_SORRY_LINE.finditer(declaration))
        if not sorry_lines:
            continue
        declaration = declaration[: sorry_lines[-1].end()].strip()
        before = content[: match.start()]
        doc_start = before.rfind("/--")
        doc_end = before.rfind("-/")
        informal = None
        if doc_start >= 0 and doc_end > doc_start and not before[doc_end + 2 :].strip():
            informal = before[doc_start + 3 : doc_end].strip()
        yield LeanProblem(
            id=match.group("name"),
            dataset="minif2f",
            split=split,
            formal_statement=f"{prelude}\n\n{declaration}\n",
            informal_statement=informal,
            environment=problem_environment,
            metadata={
                "source_path": relative.as_posix(),
                "environment_source": (
                    "AXLE compatibility check" if split == "test" else environment_source
                ),
                **({"source_header": source_header} if source_header else {}),
                "axle_compatibility": compatibility,
            },
        )


def load_lean_files(
    source: Path, *, dataset: str, split: str | None = None
) -> Iterator[LeanProblem]:
    """Treat each Lean file as one problem, preserving the complete source for AXLE."""
    files = _lean_files(source, dataset)
    if not files:
        raise DatasetError(f"no .lean files found under {source}")
    base = source if source.is_dir() else source.parent
    if dataset == "putnambench" and (base / "lean4" / "src").is_dir():
        base = base / "lean4" / "src"
    environment, environment_source = _infer_environment(source, dataset)
    for path in files:
        if dataset == "minif2f" and path.stem.lower() in {"valid", "test"}:
            aggregate = list(
                _minif2f_problems(
                    path,
                    base=base,
                    environment=environment,
                    environment_source=environment_source,
                )
            )
            if aggregate:
                yield from aggregate
                continue
        content = path.read_text(encoding="utf-8")
        if "sorry" not in content:
            continue
        content, source_header = split_source_header(content)
        relative = path.relative_to(base)
        problem_id = relative.with_suffix("").as_posix()
        problem_environment = environment
        metadata_environment_source = environment_source
        compatibility: dict[str, Any] = {}
        if dataset == "putnambench":
            content, transformations = _normalize_putnambench_source(problem_id, content)
            problem_environment = _PUTNAMBENCH_ENVIRONMENT
            metadata_environment_source = "AXLE compatibility check"
            compatibility = {
                "axle_compatibility": {
                    "status": "verified",
                    "checked_at": "2026-09-17",
                    "environment": _PUTNAMBENCH_ENVIRONMENT,
                    "import": "Mathlib",
                },
                **({"compatibility_transformations": transformations} if transformations else {}),
            }
        yield LeanProblem(
            id=problem_id,
            dataset=dataset,
            split=split or ("test" if dataset == "putnambench" else _infer_split(relative)),
            formal_statement=content,
            environment=problem_environment,
            metadata={
                "source_path": relative.as_posix(),
                "environment_source": metadata_environment_source,
                **({"source_header": source_header} if source_header else {}),
                **compatibility,
            },
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
    keys = [(problem.dataset, problem.split, problem.id) for problem in problems]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        rendered = ["/".join(key) for key in duplicates[:5]]
        raise DatasetError(f"duplicate problem keys: {', '.join(rendered)}")
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
