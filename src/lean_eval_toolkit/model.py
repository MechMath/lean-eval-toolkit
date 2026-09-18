"""OpenAI-compatible remote model client and Lean response extraction."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from lean_eval_toolkit.config import Settings
from lean_eval_toolkit.datasets import LeanProblem
from lean_eval_toolkit.sft_format import (
    SFTFormatError,
    build_sft_messages,
    build_sft_prompt,
    extract_sft_lean_code,
)

logger = logging.getLogger(__name__)


class ModelError(RuntimeError):
    """Raised for invalid or unsuccessful model API responses."""


class _RetryableModelError(ModelError):
    """A transient model API failure that may succeed on another request."""


@dataclass(slots=True)
class Generation:
    content: str
    raw_content: str
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


def build_prompt(problem: LeanProblem) -> str:
    """Build the user prompt used during supervised fine-tuning."""
    return build_sft_prompt(problem.formal_statement, problem.informal_statement)


def extract_lean_code(response: str) -> str:
    """Extract the final ``lean4`` block required by the SFT response format."""
    try:
        return extract_sft_lean_code(response)
    except SFTFormatError as exc:
        raise ModelError(str(exc)) from exc


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
        headers = dict(settings.model.extra_headers)
        api_key = settings.model.api_key.get_secret_value()
        if api_key:
            headers.setdefault("Authorization", f"Bearer {api_key}")
        self._client = httpx.AsyncClient(
            base_url=settings.model.base_url.rstrip("/") + "/",
            headers=headers,
            timeout=settings.model.timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> OpenAICompatibleClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post("chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            message = f"model API returned HTTP {exc.response.status_code}: {detail}"
            if exc.response.status_code == 429 or exc.response.status_code >= 500:
                raise _RetryableModelError(message) from exc
            raise ModelError(message) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise _RetryableModelError(f"model API request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise ModelError("model API returned an invalid chat-completions response")
        return data

    async def _request_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        for retry in range(self.settings.model.max_retries + 1):
            try:
                return await self._request(payload)
            except _RetryableModelError as exc:
                if retry == self.settings.model.max_retries:
                    raise ModelError(str(exc)) from exc
                delay = self.settings.retry.backoff_seconds * (2**retry)
                logger.warning(
                    "model request failed; retrying %s/%s in %.2fs: %s",
                    retry + 1,
                    self.settings.model.max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def generate(self, problem: LeanProblem) -> Generation:
        payload: dict[str, Any] = {
            "model": self.settings.require_model_name(),
            "messages": build_sft_messages(
                problem.formal_statement,
                problem.informal_statement,
            ),
            "temperature": self.settings.model.temperature,
            "max_tokens": self.settings.model.max_tokens,
        }
        payload.update(self.settings.model.extra_body)
        data = await self._request_with_retries(payload)
        try:
            choice = data["choices"][0]
            message = choice["message"]
            raw_content = _message_text(message["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("model API returned an invalid chat-completions response") from exc
        usage = {
            key: value
            for key, value in (data.get("usage") or {}).items()
            if isinstance(key, str) and isinstance(value, int)
        }
        if not raw_content.strip():
            reasoning = message.get("reasoning_content")
            reasoning_chars = len(reasoning) if isinstance(reasoning, str) else 0
            raise ModelError(
                "model returned an empty final response "
                f"(finish_reason={choice.get('finish_reason')!r}, "
                f"reasoning_chars={reasoning_chars})"
            )
        return Generation(
            content=extract_lean_code(raw_content),
            raw_content=raw_content,
            finish_reason=choice.get("finish_reason"),
            usage=usage,
        )
