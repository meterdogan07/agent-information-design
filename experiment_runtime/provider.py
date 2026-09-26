"""Provider-neutral mechanics for OpenAI-compatible model endpoints."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import openai


ENDPOINTS = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
}


def model_uses_temperature(provider: str, model: str, reasoning_effort: str) -> bool:
    """Preserve the trade runner's explicit-temperature compatibility rules."""
    if model == "google/gemini-3.7-flash":
        return False
    if provider == "openai" and reasoning_effort != "none":
        return False
    if model in {"gpt-5.1", "openai/gpt-5.1"} and reasoning_effort != "none":
        return False
    return True


def reasoning_options(provider: str, effort: str) -> dict[str, Any]:
    """Translate reasoning for OpenRouter and Together requests."""
    if provider == "openrouter":
        return {"reasoning": {"enabled": False} if effort == "none" else {"effort": effort}}
    if provider == "together" and effort != "none":
        return {"reasoning_effort": effort}
    return {}


def build_chat_request(*, provider, model, prompt, max_tokens, response_format,
                       reasoning_effort, backend=None, temperature=None,
                       openai_reasoning=True, openrouter_reasoning=None,
                       require_backend=False):
    """Build a request shared by all games; adapters supply schema and policy.

    ``openai_reasoning`` selects the token field and reasoning parameter.
    ``openrouter_reasoning`` preserves each study's existing handling of disabled
    reasoning. Neither retries nor game-specific response validation happen here.
    """
    request = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "response_format": response_format}
    if provider == "openai":
        request["max_completion_tokens" if openai_reasoning else "max_tokens"] = max_tokens
        if openai_reasoning and reasoning_effort is not None:
            request["reasoning_effort"] = reasoning_effort
    else:
        request["max_tokens"] = max_tokens
        extra = reasoning_options(provider, reasoning_effort)
        if provider == "openrouter":
            if openrouter_reasoning is not None:
                extra["reasoning"] = openrouter_reasoning
            if backend or require_backend:
                extra["provider"] = {"only": [backend], "allow_fallbacks": False}
        request["extra_body"] = extra
    if temperature is not None:
        request["temperature"] = temperature
    return request


def load_env_files(paths: Iterable[Path]) -> None:
    """Load ignored KEY=VALUE files without printing or replacing secrets."""
    for path in paths:
        if not path.exists():
            continue
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def create_client(provider: str, *, timeout: float) -> openai.OpenAI:
    """Create one non-retrying OpenAI SDK client for a supported endpoint."""
    try:
        base_url, key_name = ENDPOINTS[provider]
    except KeyError as error:
        names = ", ".join(sorted(ENDPOINTS))
        raise ValueError(f"provider must be one of: {names}") from error
    api_key = os.environ.get(key_name)
    if not api_key:
        raise RuntimeError(f"{key_name} is not set")
    return openai.OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
    )


@dataclass(frozen=True)
class Completion:
    """The common fields returned by an OpenAI-compatible completion."""

    content: str
    payload: dict[str, Any]
    finish_reason: Any
    response_field: str

    @property
    def response_id(self) -> Any:
        return self.payload.get("id")

    @property
    def model(self) -> Any:
        return self.payload.get("model")

    @property
    def provider(self) -> Any:
        return self.payload.get("provider")

    @property
    def usage(self) -> dict[str, Any]:
        return self.payload.get("usage") or {}


def complete_chat(
    client: openai.OpenAI,
    request: dict[str, Any],
    *,
    fallback_to_reasoning: bool = False,
) -> Completion:
    """Send one request and normalize response metadata across providers."""
    response = client.chat.completions.create(**request)
    payload = response.model_dump()
    choice = response.choices[0]
    content = choice.message.content or ""
    response_field = "content"
    if not content and fallback_to_reasoning:
        content = getattr(choice.message, "reasoning", None) or ""
        response_field = "reasoning" if content else "empty"
    return Completion(
        content=content,
        payload=payload,
        finish_reason=getattr(choice, "finish_reason", None),
        response_field=response_field,
    )


class TraceBuffer:
    """Keep per-episode traces isolated when episodes run in worker threads."""

    def __init__(self) -> None:
        self._local = threading.local()

    def start(self) -> None:
        self._local.records = []

    def end(self) -> list[dict[str, Any]]:
        records = list(getattr(self._local, "records", []))
        if hasattr(self._local, "records"):
            del self._local.records
        return records

    def append(self, record: dict[str, Any]) -> None:
        records = getattr(self._local, "records", None)
        if records is not None:
            records.append(record)


class ProviderModel:
    """Common identity and SDK transport for one configured model endpoint."""

    def __init__(
        self,
        provider: str,
        model: str,
        reasoning_effort: str | None,
        *,
        timeout: float,
    ) -> None:
        self.client = create_client(provider, timeout=timeout)
        self.provider = provider
        self.model = model
        self.reasoning_effort = reasoning_effort

    def complete(
        self,
        request: dict[str, Any],
        *,
        fallback_to_reasoning: bool = False,
    ) -> Completion:
        return complete_chat(
            self.client,
            request,
            fallback_to_reasoning=fallback_to_reasoning,
        )


class TracedProviderModel(ProviderModel):
    """Provider model with traces isolated between concurrent episodes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.trace = TraceBuffer()

    def start_trace(self) -> None:
        self.trace.start()

    def end_trace(self) -> list[dict[str, Any]]:
        return self.trace.end()

    def record(self, value: dict[str, Any]) -> None:
        self.trace.append(value)


def token_counts(usage: dict[str, Any] | None) -> dict[str, int]:
    """Normalize token counters used by OpenAI-compatible providers."""
    usage = usage or {}
    details = usage.get("completion_tokens_details") or {}
    return {
        "input_tokens": int(
            usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
        ),
        "output_tokens": int(
            usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        ),
        "reasoning_tokens": int(details.get("reasoning_tokens", 0) or 0),
    }
