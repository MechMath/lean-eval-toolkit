from types import SimpleNamespace

import pytest

from lean_eval_toolkit.config import Settings
from lean_eval_toolkit.datasets import LeanProblem
from lean_eval_toolkit.verifier import AxleVerifier


@pytest.mark.asyncio
async def test_axle_pass_requires_no_failed_declarations(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object):
            captured.update(kwargs)

        async def verify_proof(self, **kwargs: object) -> object:
            captured.update(kwargs)
            messages = SimpleNamespace(errors=[], warnings=[])
            return SimpleNamespace(
                okay=True,
                failed_declarations=[],
                lean_messages=messages,
                tool_messages=messages,
                timings={"total": 1},
                info=None,
            )

        async def close(self) -> None:
            pass

    monkeypatch.setattr("lean_eval_toolkit.verifier.AxleClient", FakeClient)
    settings = Settings(_env_file=None, axle_api_key="axle-secret")
    problem = LeanProblem(
        id="demo",
        dataset="manual",
        formal_statement="theorem demo : True := by sorry",
        environment="lean-4.26.0",
    )

    async with AxleVerifier(settings) as verifier:
        result = await verifier.verify(problem, "theorem demo : True := by trivial")

    assert result.passed
    assert captured["api_key"] == "axle-secret"
    assert captured["formal_statement"] == problem.formal_statement
    assert captured["environment"] == "lean-4.26.0"
    assert captured["permitted_sorries"] == []
