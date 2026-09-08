"""OpenAI-compatible remote model client and Lean response extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from lean_eval_toolkit.config import Settings
from lean_eval_toolkit.datasets import LeanProblem

DEFAULT_SYSTEM_PROMPT = """You are an expert Lean 4 theorem prover.
Return a complete Lean source file that proves the requested declarations.
Replace every `sorry` in the supplied file with sound code. Do not change theorem statements,
introduce axioms, use `sorry`, or use unsafe features. Return only Lean code, preferably in one
```lean fenced block.
"""

_LEAN_FENCE = re.compile(r"```(?:lean4?|Lean4?)?\s*\n(?P<code>.*?)```", re.DOTALL)


class ModelError(RuntimeError):
    """Raised for invalid or unsuccessful model API responses."""


@dataclass(slots=True)
class Generation:
    content: str
    raw_content: str
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


def build_prompt(problem: LeanProblem) -> str:
    """Build a stable benchmark prompt without leaking reference proofs."""
    sections = [f"Problem ID: {problem.id}"]
    if problem.informal_statement:
        sections.extend(["Informal statement:", problem.informal_statement])
    sections.extend(
        [
            "Complete this Lean file by replacing every `sorry`:",
            "```lean",
            problem.formal_statement,
            "```",
        ]
    )
    return "\n\n".join(sections)


def extract_lean_code(response: str) -> str:
    """Extract the first fenced Lean block, or use a plain-code response as-is."""
    match = _LEAN_FENCE.search(response)
    code = match.group("code") if match else response
    code = code.strip()
    if not code:
        raise ModelError("model returned an empty response")
    return code + "\n"


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        if any(parts):
            return "".join(parts)
    raise ModelError("response choice does not contain text content")


class OpenAICompatibleClient:
    """Minimal chat-completions client suitable for vLLM and commercial APIs."""

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        headers = dict(settings.model_extra_headers)
        api_key = settings.model_api_key.get_secret_value()
        if api_key:
            headers.setdefault("Authorization", f"Bearer {api_key}")
        self._client = httpx.AsyncClient(
            base_url=settings.model_base_url.rstrip("/") + "/",
            headers=headers,
            timeout=settings.model_timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> OpenAICompatibleClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def generate(self, problem: LeanProblem) -> Generation:
        payload: dict[str, Any] = {
            "model": self.settings.require_model_name(),
            "messages": [
                {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(problem)},
            ],
            "temperature": self.settings.model_temperature,
            "max_tokens": self.settings.model_max_tokens,
        }
        payload.update(self.settings.model_extra_body)
        try:
            response = await self._client.post("chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            message = f"model API returned HTTP {exc.response.status_code}: {detail}"
            raise ModelError(message) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelError(f"model API request failed: {exc}") from exc
        try:
            choice = data["choices"][0]
            raw_content = _message_text(choice["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("model API returned an invalid chat-completions response") from exc
        usage = {
            key: value
            for key, value in (data.get("usage") or {}).items()
            if isinstance(key, str) and isinstance(value, int)
        }
        return Generation(
            content=extract_lean_code(raw_content),
            raw_content=raw_content,
            finish_reason=choice.get("finish_reason"),
            usage=usage,
        )
