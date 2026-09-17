import httpx
import pytest

from lean_eval_toolkit.config import Settings
from lean_eval_toolkit.datasets import LeanProblem
from lean_eval_toolkit.model import (
    ModelError,
    OpenAICompatibleClient,
    build_prompt,
    extract_lean_code,
)


@pytest.fixture
def problem() -> LeanProblem:
    return LeanProblem(
        id="demo",
        dataset="manual",
        formal_statement="import Mathlib\ntheorem demo : True := by sorry\n",
        informal_statement="Prove True.",
    )


def test_build_prompt_includes_informal_statement_as_lean_comment(problem: LeanProblem) -> None:
    prompt = build_prompt(problem)
    assert "Complete the following Lean 4 code:" in prompt
    assert problem.formal_statement.rstrip() in prompt
    assert "-- Informal statement:\n-- Prove True.\n\nimport Mathlib" in prompt
    assert prompt.endswith(
        "The plan should highlight key ideas, intermediate lemmas, and proof structures that will "
        "guide the construction of the final formal proof."
    )


def test_extracts_lean_fence() -> None:
    assert extract_lean_code("Explanation\n```lean4\ntheorem x : True := by trivial\n```") == (
        "theorem x : True := by trivial\n"
    )


def test_extracts_final_lean4_fence_instead_of_reasoning_example() -> None:
    response = """Plan with an example:
```
example : True := by trivial
```

```lean4
theorem final : True := by trivial
```
"""
    assert extract_lean_code(response) == "theorem final : True := by trivial\n"


def test_rejects_response_without_labelled_lean4_fence() -> None:
    with pytest.raises(ModelError, match="fenced `lean4` code block"):
        extract_lean_code("```lean\ntheorem x : True := by trivial\n```")


@pytest.mark.asyncio
async def test_calls_openai_compatible_endpoint(problem: LeanProblem) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://models.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer token"
        body = __import__("json").loads(request.content)
        assert body["model"] == "prover"
        assert body["top_p"] == 0.9
        assert body["messages"] == [{"role": "user", "content": build_prompt(problem)}]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "```lean4\ntheorem demo : True := by trivial\n```"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8},
            },
        )

    settings = Settings(
        _env_file=None,
        model_base_url="https://models.example/v1",
        model_api_key="token",
        model_name="prover",
        model_extra_body={"top_p": 0.9},
    )
    async with OpenAICompatibleClient(settings, transport=httpx.MockTransport(handler)) as client:
        generation = await client.generate(problem)

    assert generation.content.endswith("by trivial\n")
    assert generation.usage["completion_tokens"] == 8


@pytest.mark.asyncio
async def test_reports_model_http_error(problem: LeanProblem) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(401, text="bad key"))
    settings = Settings(_env_file=None, model_name="prover")
    async with OpenAICompatibleClient(settings, transport=transport) as client:
        with pytest.raises(ModelError, match="HTTP 401"):
            await client.generate(problem)


@pytest.mark.asyncio
async def test_reports_empty_reasoning_response_without_exposing_reasoning(
    problem: LeanProblem,
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "", "reasoning_content": "private chain of thought"},
                        "finish_reason": "length",
                    }
                ]
            },
        )
    )
    settings = Settings(_env_file=None, model_name="prover")
    async with OpenAICompatibleClient(settings, transport=transport) as client:
        with pytest.raises(ModelError, match=r"finish_reason='length'.*reasoning_chars=24") as exc:
            await client.generate(problem)
    assert "private chain" not in str(exc.value)
