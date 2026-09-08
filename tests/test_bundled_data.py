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
            "829203975f27262b9009f5f6f7498c4e8a694b4966b0ed13564837fb27b61532",
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
    assert {record["environment"] for record in records} == {"lean-4.27.0"}
    assert all("sorry" in record["formal_statement"] for record in records)
    assert hashlib.sha256(payload).hexdigest() == expected_sha256
