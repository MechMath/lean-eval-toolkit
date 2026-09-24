from types import SimpleNamespace

import pytest
from axle.exceptions import AxleInternalError, AxleInvalidArgument

from lean_eval_toolkit.config import load_settings
from lean_eval_toolkit.datasets import LeanProblem
from lean_eval_toolkit.verifier import AxleVerifier


def _successful_response() -> SimpleNamespace:
    messages = SimpleNamespace(errors=[], warnings=[])
    return SimpleNamespace(
        okay=True,
        failed_declarations=[],
        lean_messages=messages,
        tool_messages=messages,
        timings={"total": 1},
        info=None,
    )


@pytest.mark.asyncio
async def test_axle_pass_requires_no_failed_declarations(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object):
            captured.update(kwargs)

        async def verify_proof(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return _successful_response()

        async def close(self) -> None:
            pass

    monkeypatch.setattr("lean_eval_toolkit.verifier.AxleClient", FakeClient)
    settings = load_settings(
        env_file=None, overrides={"axle": {"api_key": "axle-secret"}}
    )
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
    assert captured["content"] == "theorem demo : True := by trivial"
    assert captured["environment"] == "lean-4.26.0"
    assert captured["permitted_sorries"] == []


@pytest.mark.asyncio
async def test_retries_axle_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class FakeClient:
        def __init__(self, **_: object):
            pass

        async def verify_proof(self, **_: object) -> object:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise AxleInternalError("temporary server failure")
            return _successful_response()

        async def close(self) -> None:
            pass

    monkeypatch.setattr("lean_eval_toolkit.verifier.AxleClient", FakeClient)
    settings = load_settings(
        env_file=None,
        overrides={
            "axle": {"max_retries": 2},
            "retry": {"backoff_seconds": 0},
        },
    )
    problem = LeanProblem(
        id="demo",
        dataset="manual",
        formal_statement="theorem demo : True := by sorry",
        environment="lean-4.30.0",
    )

    async with AxleVerifier(settings) as verifier:
        result = await verifier.verify(problem, "theorem demo : True := by trivial")

    assert result.passed
    assert calls == 3


@pytest.mark.asyncio
async def test_stops_axle_retries_at_configured_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeClient:
        def __init__(self, **_: object):
            pass

        async def verify_proof(self, **_: object) -> object:
            nonlocal calls
            calls += 1
            raise AxleInternalError("persistent server failure")

        async def close(self) -> None:
            pass

    monkeypatch.setattr("lean_eval_toolkit.verifier.AxleClient", FakeClient)
    settings = load_settings(
        env_file=None,
        overrides={
            "axle": {"max_retries": 2},
            "retry": {"backoff_seconds": 0},
        },
    )
    problem = LeanProblem(
        id="demo",
        dataset="manual",
        formal_statement="theorem demo : True := by sorry",
        environment="lean-4.30.0",
    )

    async with AxleVerifier(settings) as verifier:
        with pytest.raises(AxleInternalError, match="persistent server failure"):
            await verifier.verify(problem, "theorem demo : True := by trivial")

    assert calls == 3


@pytest.mark.asyncio
async def test_does_not_retry_axle_invalid_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class FakeClient:
        def __init__(self, **_: object):
            pass

        async def verify_proof(self, **_: object) -> object:
            nonlocal calls
            calls += 1
            raise AxleInvalidArgument("bad Lean input")

        async def close(self) -> None:
            pass

    monkeypatch.setattr("lean_eval_toolkit.verifier.AxleClient", FakeClient)
    settings = load_settings(
        env_file=None,
        overrides={
            "axle": {"max_retries": 3},
            "retry": {"backoff_seconds": 0},
        },
    )
    problem = LeanProblem(
        id="demo",
        dataset="manual",
        formal_statement="theorem demo : True := by sorry",
        environment="lean-4.30.0",
    )

    async with AxleVerifier(settings) as verifier:
        with pytest.raises(AxleInvalidArgument, match="bad Lean input"):
            await verifier.verify(problem, "invalid")

    assert calls == 1
