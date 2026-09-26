"""OpenAI-compatible model calls with explicit provider-specific settings."""

from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any, Callable, Optional

from experiment_runtime import (
    ENDPOINTS,
    ProviderModel,
    load_env_files,
)

from .game import action_schema
from experiment_runtime.models import generation_settings
from experiment_runtime.provider import build_chat_request, model_uses_temperature, reasoning_options


PROVIDERS = ENDPOINTS
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high")


def load_env(path: Path) -> None:
    """Load one ignored credential file through the shared runtime."""
    load_env_files([path])


class Model(ProviderModel):
    """Small client for one OpenAI-compatible chat-completion endpoint."""

    def __init__(
        self,
        provider: str,
        model: str,
        reasoning_effort: str,
        max_tokens: int,
        openrouter_provider: Optional[str] = None,
        temperature: Optional[float] = None,
        progress: Optional[Callable[[str], None]] = None,
    ):
        # Reasoning responses can take longer late in a negotiation because the
        # complete dialogue is passed on every turn.
        super().__init__(provider, model, reasoning_effort,
                         timeout=generation_settings("trade")["timeout_seconds"])
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.openrouter_provider = openrouter_provider
        self.progress = progress
        self.call_log: list[dict[str, Any]] = []
        self.logical_call_count = 0

    def _notify(self, message: str) -> None:
        """Emit optional live progress without coupling the client to a CLI."""
        callback = getattr(self, "progress", None)
        if callback is not None:
            callback(message)

    def uses_temperature(self) -> bool:
        return self.temperature is not None and model_uses_temperature(
            self.provider, self.model, self.reasoning_effort
        )

    def _build_request(
        self,
        prompt: str,
        response_format: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the one provider-specific part of an otherwise common request."""
        return build_chat_request(
            provider=self.provider, model=self.model, prompt=prompt,
            max_tokens=self.max_tokens, response_format=response_format or action_schema(),
            reasoning_effort=self.reasoning_effort, backend=self.openrouter_provider,
            temperature=self.temperature if self.uses_temperature() else None,
            openai_reasoning=not self.model.startswith("gpt-4o-mini"),
        )

    def _send(self, request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Send one request and retain the selected response field."""
        completion = self.complete(request, fallback_to_reasoning=True)
        completion.payload["_selected_response_field"] = (
            completion.response_field
        )
        return completion.content, completion.payload

    def _record_response(
        self,
        response_data: dict[str, Any],
        request: dict[str, Any],
        raw_response: str,
        prompt: str,
        logical_call_id: int,
        attempt: int,
        started: float,
    ) -> None:
        """Log the exact exchange, provenance, and requested backend."""
        actual_provider = response_data.get("provider")
        if not actual_provider:
            actual_provider = {
                "openai": "OpenAI",
                "together": "Together",
            }.get(self.provider)
        choices = response_data.get("choices") or []
        finish_reason = choices[0].get("finish_reason") if choices else None
        record = {
            "logical_call_id": logical_call_id,
            "attempt": attempt,
            "latency_seconds": time.monotonic() - started,
            "request": copy.deepcopy(request),
            "raw_response": raw_response,
            "response_id": response_data.get("id"),
            "reported_model": response_data.get("model"),
            "reported_provider": actual_provider,
            "finish_reason": finish_reason,
            "response_field": response_data.get("_selected_response_field"),
            "prompt_characters": len(prompt),
            "usage": response_data.get("usage"),
        }
        self.call_log.append(record)
        if (
            self.openrouter_provider
            and actual_provider
            and actual_provider.lower() != self.openrouter_provider.lower()
        ):
            message = (
                f"OpenRouter used {actual_provider}, "
                f"expected {self.openrouter_provider}"
            )
            record["error"] = f"RuntimeError: {message}"
            raise RuntimeError(message)

    def _record_error(
        self,
        request: dict[str, Any],
        prompt: str,
        logical_call_id: int,
        attempt: int,
        started: float,
        error: Exception,
    ) -> None:
        """Retain failed attempts so retries remain fully auditable."""
        self.call_log.append({
            "logical_call_id": logical_call_id,
            "attempt": attempt,
            "latency_seconds": time.monotonic() - started,
            "request": copy.deepcopy(request),
            "raw_response": None,
            "response_id": None,
            "reported_model": None,
            "reported_provider": None,
            "finish_reason": None,
            "prompt_characters": len(prompt),
            "usage": None,
            "error": f"{type(error).__name__}: {error}",
        })

    def __call__(
        self,
        prompt: str,
        response_format: Optional[dict[str, Any]] = None,
    ) -> str:
        """Return one structured response and retain call provenance."""
        request = self._build_request(prompt, response_format)
        logical_call_id = getattr(self, "logical_call_count", 0) + 1
        self.logical_call_count = logical_call_id
        error: Optional[Exception] = None
        for attempt in range(1, 4):
            started = time.monotonic()
            self._notify(
                f"model call {logical_call_id}, attempt {attempt}: started"
            )
            try:
                content, response_data = self._send(request)
            except Exception as caught:
                self._record_error(
                    request, prompt, logical_call_id, attempt, started, caught
                )
                error = caught
                self._notify(
                    f"model call {logical_call_id}, attempt {attempt}: "
                    f"failed after {time.monotonic() - started:.1f}s "
                    f"({type(caught).__name__}: {caught})"
                )
            else:
                try:
                    self._record_response(
                        response_data,
                        request,
                        content,
                        prompt,
                        logical_call_id,
                        attempt,
                        started,
                    )
                    if content:
                        record = self.call_log[-1]
                        provider = record.get("reported_provider") or self.provider
                        self._notify(
                            f"model call {logical_call_id}, attempt {attempt}: "
                            f"completed in {record['latency_seconds']:.1f}s "
                            f"via {provider}"
                        )
                        return content
                    error = RuntimeError("model returned an empty response")
                    self.call_log[-1]["error"] = (
                        "RuntimeError: model returned an empty response"
                    )
                    self._notify(
                        f"model call {logical_call_id}, attempt {attempt}: "
                        "empty response; retrying"
                    )
                except Exception as caught:
                    # The response itself is already in the log. This branch
                    # covers an enforced OpenRouter-provider mismatch.
                    error = caught
                    self._notify(
                        f"model call {logical_call_id}, attempt {attempt}: "
                        f"rejected ({type(caught).__name__}: {caught})"
                    )
            if attempt < 3:
                self._notify(
                    f"model call {logical_call_id}: retrying after {attempt}s"
                )
                time.sleep(attempt)
        raise RuntimeError(f"model request failed after 3 attempts: {error}")
