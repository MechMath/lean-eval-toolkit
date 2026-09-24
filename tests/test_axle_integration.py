"""Opt-in real AXLE regression for declaration-only proof submissions."""

import json
import os
from pathlib import Path

import pytest

from lean_eval_toolkit.config import load_settings
from lean_eval_toolkit.datasets import LeanProblem
from lean_eval_toolkit.verifier import AxleVerifier

FIXTURE = json.loads((Path(__file__).parent / "fixtures/theorem_only.json").read_text())
pytestmark = pytest.mark.skipif(
    os.environ.get("AXLE_INTEGRATION") != "1", reason="set AXLE_INTEGRATION=1 to use real AXLE"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate_key", "expected_pass"),
    [
        ("passing", True),
        ("signature_changed", False),
        ("unresolved_sorry", False),
        ("missing_import", False),
        ("tactic_body_only", False),
    ],
)
async def test_theorem_only_verification(candidate_key: str, expected_pass: bool) -> None:
    settings = load_settings()
    if not settings.axle.api_key.get_secret_value():
        pytest.skip("AXLE_API_KEY is required for AXLE integration")
    formal_key = (
        "missing_import_formal_statement"
        if candidate_key == "missing_import"
        else "formal_statement"
    )
    problem = LeanProblem(
        id="theorem_only_smoke",
        dataset="integration",
        formal_statement=FIXTURE[formal_key],
        environment="lean-4.30.0",
    )
    async with AxleVerifier(settings) as verifier:
        result = await verifier.verify(problem, FIXTURE[candidate_key])
    assert result.passed is expected_pass, result.to_dict()
