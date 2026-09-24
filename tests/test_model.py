import httpx
import pytest

from lean_eval_toolkit.config import load_settings
from lean_eval_toolkit.datasets import LeanProblem
from lean_eval_toolkit.model import (
    ModelError,
    OpenAICompatibleClient,
    build_prompt,
    extract_lean_code,
)
from lean_eval_toolkit.test_templates import load_test_template


@pytest.fixture
def problem() -> LeanProblem:
    return LeanProblem(
        id="demo",
        dataset="manual",
        formal_statement="import Mathlib\ntheorem demo : True := by sorry\n",
        informal_statement="Prove True.",
    )


def test_build_prompt_uses_selected_test_template(problem: LeanProblem) -> None:
    prompt = build_prompt(problem)
    assert "Complete the following Lean 4 code:" in prompt
    assert problem.formal_statement.rstrip() in prompt
    assert "Informal statement" not in prompt
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


def test_accepts_lean_fence_as_configured_fallback() -> None:
    assert extract_lean_code("```lean\ntheorem x : True := by trivial\n```") == (
        "theorem x : True := by trivial\n"
    )


def test_rejects_response_without_extractable_lean() -> None:
    with pytest.raises(ModelError, match="did not match"):
        extract_lean_code("I could not solve this problem.")


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

    settings = load_settings(
        env_file=None,
        overrides={
            "model": {
                "base_url": "https://models.example/v1",
                "api_key": "token",
                "name": "prover",
                "extra_body": {"top_p": 0.9},
            }
        },
    )
    async with OpenAICompatibleClient(settings, transport=httpx.MockTransport(handler)) as client:
        generation = await client.generate(problem)

    assert generation.content.endswith("by trivial\n")
    assert generation.usage["completion_tokens"] == 8
    assert generation.extraction_strategy == "final_lean4_fence"
    assert generation.used_extraction_fallback


@pytest.mark.asyncio
async def test_passes_model_chat_template_without_rendering_special_tokens(
    problem: LeanProblem,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        assert body["chat_template"] == "<bos>{{ messages }}<eos>"
        assert "<bos>" not in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "theorem demo : True := by trivial"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    settings = load_settings(
        env_file=None,
        overrides={
            "model": {
                "name": "prover",
                "chat_template": "<bos>{{ messages }}<eos>",
            }
        },
    )
    async with OpenAICompatibleClient(
        settings, transport=httpx.MockTransport(handler)
    ) as client:
        generation = await client.generate(problem)

    assert generation.extraction_strategy == "raw_lean"


@pytest.mark.asyncio
async def test_reports_model_http_error(problem: LeanProblem) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(401, text="bad key"))
    settings = load_settings(env_file=None, overrides={"model": {"name": "prover"}})
    async with OpenAICompatibleClient(settings, transport=transport) as client:
        with pytest.raises(ModelError, match="HTTP 401"):
            await client.generate(problem)


@pytest.mark.asyncio
async def test_repair_request_sends_history_and_uses_revised_heading(problem: LeanProblem) -> None:
    template = load_test_template("lean-plan-repair-v3")
    history = [
        *template.render(problem),
        {"role": "assistant", "content": "failed raw response"},
        {"role": "tool", "content": "Lean compiler feedback:\n\nunknown tactic"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert __import__("json").loads(request.content)["messages"] == history
        return httpx.Response(200, json={"choices": [{"message": {"content": (
            "### Revised Proof Plan\nUse trivial.\n### Lean Proof\n"
            "```lean4\ntheorem demo : True := by trivial\n```"
        )}}]})

    settings = load_settings(env_file=None, overrides={"model": {"name": "prover"}})
    async with OpenAICompatibleClient(
        settings, transport=httpx.MockTransport(handler), test_template=template
    ) as client:
        result = await client.generate(problem, messages=history, repair_round=1)
    assert result.extraction_strategy == "strict_revised_lean_proof"


@pytest.mark.asyncio
async def test_retries_transient_model_request_failure(problem: LeanProblem) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("temporary connection failure", request=request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "```lean4\ntheorem demo : True := by trivial\n```"
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    settings = load_settings(
        env_file=None,
        overrides={
            "model": {"name": "prover", "max_retries": 2},
            "retry": {"backoff_seconds": 0},
        },
    )
    async with OpenAICompatibleClient(
        settings, transport=httpx.MockTransport(handler)
    ) as client:
        generation = await client.generate(problem)

    assert generation.content.endswith("by trivial\n")
    assert calls == 3


@pytest.mark.asyncio
async def test_stops_model_retries_at_configured_maximum(problem: LeanProblem) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("still unavailable", request=request)

    settings = load_settings(
        env_file=None,
        overrides={
            "model": {"name": "prover", "max_retries": 2},
            "retry": {"backoff_seconds": 0},
        },
    )
    async with OpenAICompatibleClient(
        settings, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ModelError, match="model API request failed") as exc:
            await client.generate(problem)

    assert type(exc.value) is ModelError
    assert calls == 3


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
    settings = load_settings(env_file=None, overrides={"model": {"name": "prover"}})
    async with OpenAICompatibleClient(settings, transport=transport) as client:
        with pytest.raises(ModelError, match=r"finish_reason='length'.*reasoning_chars=24") as exc:
            await client.generate(problem)
    assert "private chain" not in str(exc.value)
