"""The complete model transport used by the current experiment.

Only two API styles are needed: OpenAI directly and OpenRouter's
OpenAI-compatible endpoint. Every attempt is recorded with its full prompt,
raw response, endpoint metadata, schema, usage, and error.
"""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any
from experiment_runtime.models import generation_settings
from experiment_runtime.provider import build_chat_request

from experiment_runtime import (
    TracedProviderModel,
    load_env_files,
    token_counts,
)

REPOSITORY = Path(__file__).resolve().parents[1]


def load_env() -> None:
    """Load the first local ignored env file without printing credentials."""
    load_env_files((
        REPOSITORY / "src/.env",
        REPOSITORY / ".env",
        REPOSITORY.parent / ".env",
    ))


class ModelClient(TracedProviderModel):
    """Thread-safe tracing wrapper around one pinned model endpoint."""

    def __init__(
        self,
        provider: str,
        model: str,
        backend: str | None,
        reasoning_effort: str,
        retries: int,
        timeout: float = generation_settings("bargaining")["timeout_seconds"],
    ) -> None:
        if provider not in {"openai", "openrouter"}:
            raise ValueError("provider must be openai or openrouter")
        super().__init__(provider, model, reasoning_effort, timeout=timeout)
        self.backend = backend
        self.retries = retries

    def __call__(
        self,
        prompt: str,
        temperature: float | None,
        max_tokens: int,
        response_format: dict[str, Any],
    ) -> str:
        started = time.time()
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            request = build_chat_request(
                provider=self.provider, model=self.model, prompt=prompt,
                max_tokens=max_tokens, response_format=response_format,
                reasoning_effort=self.reasoning_effort, backend=self.backend,
                temperature=temperature, openai_reasoning=self.model.startswith("gpt-5"),
                openrouter_reasoning={"effort": self.reasoning_effort}, require_backend=True,
            )
            record = {
                "provider": self.provider,
                "requested_model": self.model,
                "requested_backend": self.backend,
                "returned_model": None,
                "returned_provider": None,
                "response_id": None,
                "system_fingerprint": None,
                "prompt": prompt,
                "response": "",
                "response_format": response_format,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "reasoning_effort": self.reasoning_effort,
                "finish_reason": None,
                "usage": None,
                "attempt": attempt,
                "elapsed_seconds": None,
                "error": None,
            }
            try:
                completion = self.complete(request)
                record.update({
                    "returned_model": completion.model,
                    "returned_provider": completion.provider,
                    "response_id": completion.response_id,
                    "system_fingerprint": completion.payload.get("system_fingerprint"),
                    "response": completion.content,
                    "finish_reason": completion.finish_reason,
                    "usage": completion.usage,
                })
                if not completion.content:
                    raise ValueError("provider returned an empty response")
            except Exception as exc:
                last_error = exc
                record["error"] = f"{type(exc).__name__}: {exc}"
                record["elapsed_seconds"] = round(time.time() - started, 4)
                self.record(record)
                if attempt < self.retries:
                    time.sleep(min(2 ** (attempt - 1), 4))
            else:
                record["elapsed_seconds"] = round(time.time() - started, 4)
                self.record(record)
                return completion.content
        assert last_error is not None
        raise last_error


def summarize_trace(trace: list[dict[str, Any]]) -> dict[str, int]:
    result = {
        "call_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
    }
    for call in trace:
        if call.get("error"):
            continue
        result["call_count"] += 1
        counts = token_counts(call.get("usage"))
        for name in counts:
            result[name] += counts[name]
    return result


def validate_trace(
    trace: list[dict[str, Any]],
    expected_calls: int,
    provider: str,
    backend: str | None,
    model: str,
    reasoning_effort: str,
    max_tokens: int,
) -> str | None:
    successful = [call for call in trace if not call.get("error")]
    if len(successful) != expected_calls:
        return f"trace has {len(successful)} successful calls; expected {expected_calls}"
    for index, call in enumerate(successful, 1):
        if not call.get("response_id") or not call.get("returned_model"):
            return f"call {index} lacks endpoint provenance"
        if call.get("provider") != provider:
            return f"call {index} used the wrong API provider"
        if call.get("requested_model") != model:
            return f"call {index} requested the wrong model"
        if call.get("returned_model") != model:
            return f"call {index} returned a different model"
        if call.get("requested_backend") != backend:
            return f"call {index} requested the wrong backend"
        if call.get("reasoning_effort") != reasoning_effort:
            return f"call {index} used the wrong reasoning effort"
        if call.get("max_tokens") != max_tokens:
            return f"call {index} used the wrong output limit"
        if call.get("temperature") is not None:
            return f"call {index} did not use provider-default temperature"
        if str(call.get("finish_reason") or "").lower() in {
            "length", "max_tokens", "incomplete"
        }:
            return f"call {index} ended at its output limit"
        if provider == "openrouter" and backend:
            actual = str(call.get("returned_provider") or "")
            if actual.lower() != backend.lower():
                return f"call {index} used {actual or 'UNKNOWN'}, expected {backend}"
    return None
