import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("relative_path", "expected_count", "expected_splits", "expected_sha256"),
    [
        (
            "data/minif2f/problems.jsonl",
            498,
            {"validation": 256, "test": 242},
            "e2ff75eed3893edf955aa77d54bdf3d0cee4e839212164fdbae4677ba07aa822",
        ),
        (
            "data/putnambench/problems.jsonl",
            672,
            {"test": 672},
            "c77b8f54caa9f0d882292cf508e7a4d26bfbb5248ee9a262c7827ae94b9d1e84",
        ),
    ],
)
def test_bundled_snapshot_integrity(
    relative_path: str,
    expected_count: int,
    expected_splits: dict[str, int],
    expected_sha256: str,
) -> None:
    payload = (ROOT / relative_path).read_bytes()
    records = [json.loads(line) for line in payload.splitlines()]

    assert len(records) == expected_count
    assert Counter(record["split"] for record in records) == expected_splits
    assert all("sorry" in record["formal_statement"] for record in records)
    assert all("Copyright" not in record["formal_statement"] for record in records)
    assert all("Released under" not in record["formal_statement"] for record in records)
    assert all("Authors:" not in record["formal_statement"] for record in records)
    if relative_path.startswith("data/minif2f/"):
        assert all("source_header" in record["metadata"] for record in records)
        test = [record for record in records if record["split"] == "test"]
        validation = [record for record in records if record["split"] == "validation"]
        assert {record["environment"] for record in test} == {"lean-4.30.0"}
        assert {record["formal_statement"].splitlines()[0] for record in test} == {
            "import Mathlib"
        }
        assert {
            record["metadata"]["axle_compatibility"]["status"] for record in test
        } == {"verified"}
        assert {record["environment"] for record in validation} == {"lean-4.27.0"}
        assert {record["formal_statement"].splitlines()[0] for record in validation} == {
            "import MiniF2F.ProblemImports"
        }
        assert {
            record["metadata"]["axle_compatibility"]["status"] for record in validation
        } == {"unverified"}
    else:
        assert {record["environment"] for record in records} == {"lean-4.27.0"}
    assert hashlib.sha256(payload).hexdigest() == expected_sha256
