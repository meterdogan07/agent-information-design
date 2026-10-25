"""Small traced client for OpenAI and pinned OpenRouter calls."""

from __future__ import annotations

import time
from experiment_runtime.models import generation_settings
from experiment_runtime.provider import build_chat_request

from experiment_runtime import (
    TracedProviderModel,
    token_counts,
)


class TracedLLM(TracedProviderModel):
    """Call one model and retain the complete prompt, response, and metadata."""

    def __init__(self, provider, model, reasoning_effort, max_tokens,
                 openrouter_provider=None):
        if provider not in {"openai", "openrouter", "anthropic"}:
            raise ValueError("provider must be openai, openrouter, or anthropic")
        super().__init__(provider, model, reasoning_effort,
                         timeout=generation_settings("auction")["timeout_seconds"])
        self.max_tokens = max_tokens
        self.openrouter_provider = openrouter_provider

    def _request(self, prompt, max_tokens):
        response_format = {"type": "json_object"}
        if self.provider == "anthropic":
            # Same output fields as the existing prompt; native Messages needs
            # a schema instead of OpenAI's schema-free JSON-object mode.
            if '"bid": <number>' in prompt:
                field = "bid"
            elif '"ask": <number>' in prompt:
                field = "ask"
            else:
                raise ValueError("Direct Anthropic supports the standard single-unit auction prompt")
            response_format = {"type": "json_schema", "json_schema": {"schema": {
                "type": "object", "properties": {"reasoning": {"type": "string"},
                field: {"type": "number"}}, "required": ["reasoning", field],
                "additionalProperties": False}}}
        return build_chat_request(
            provider=self.provider, model=self.model, prompt=prompt, max_tokens=max_tokens,
            response_format=response_format, reasoning_effort=self.reasoning_effort,
            backend=self.openrouter_provider,
            openai_reasoning=not self.model.startswith("gpt-4o-mini"),
            openrouter_reasoning=({"enabled": False} if self.reasoning_effort in {None, "none"}
                                  else {"effort": self.reasoning_effort}),
        )

    def __call__(self, prompt, max_tokens=None):
        token_limit = max_tokens or self.max_tokens
        request = self._request(prompt, token_limit)
        retries, final_error = [], None

        max_attempts = generation_settings("auction")["attempts"]
        for attempt in range(1, max_attempts + 1):
            started = time.monotonic()
            try:
                completion = self.complete(request)
                data = completion.payload
                content = completion.content
                finish = completion.finish_reason
                returned_provider = data.get("provider") or (
                    "OpenAI" if self.provider == "openai" else None
                )
                error = None
                if not content.strip():
                    error = "empty response"
                elif str(finish).lower() != "stop":
                    error = f"incomplete response: {finish}"
                elif (self.openrouter_provider and returned_provider
                      and returned_provider.lower() != self.openrouter_provider.lower()):
                    error = (
                        f"OpenRouter used {returned_provider}; expected "
                        f"{self.openrouter_provider}"
                    )
                record = {
                    "attempt": attempt,
                    "returned_model": data.get("model"),
                    "returned_provider": returned_provider,
                    "response_id": data.get("id"),
                    "response": content,
                    "finish_reason": finish,
                    "usage": data.get("usage") or {},
                    "elapsed_seconds": time.monotonic() - started,
                    "error": error,
                }
                if self.provider == "anthropic":
                    record.update(native_request=request,
                                  native_response=data.get("anthropic_response"))
                if error:
                    retries.append(record)
                    final_error = RuntimeError(error)
                else:
                    self.record({
                        "provider": self.provider,
                        "requested_model": self.model,
                        "openrouter_provider": self.openrouter_provider,
                        "prompt": prompt,
                        "temperature": "provider_default",
                        "temperature_sent": False,
                        "max_tokens": token_limit,
                        "reasoning_effort": self.reasoning_effort,
                        "attempts": attempt,
                        "attempt_history": retries,
                        **{key: value for key, value in record.items() if key != "attempt"},
                    })
                    return content
            except Exception as error:  # provider SDK errors share no stable type
                final_error = error
                retries.append({
                    "attempt": attempt,
                    "returned_model": None,
                    "returned_provider": None,
                    "response_id": None,
                    "response": None,
                    "finish_reason": None,
                    "usage": {},
                    "elapsed_seconds": time.monotonic() - started,
                    "error": str(error),
                })
            if attempt < max_attempts:
                time.sleep(attempt)

        self.record({
            "provider": self.provider,
            "requested_model": self.model,
            "openrouter_provider": self.openrouter_provider,
            "prompt": prompt,
            "response": None,
            "temperature": "provider_default",
            "temperature_sent": False,
            "max_tokens": token_limit,
            "reasoning_effort": self.reasoning_effort,
            "attempts": max_attempts,
            "attempt_history": retries,
            "error": str(final_error),
        })
        raise RuntimeError(f"model request failed after {max_attempts} attempts: {final_error}")


def make_llm(provider, model, reasoning_effort=None,
             default_max_tokens=generation_settings("auction")["max_tokens"],
             openrouter_provider=None):
    return TracedLLM(
        provider, model, reasoning_effort, default_max_tokens,
        openrouter_provider=openrouter_provider,
    )


def run_traced(llm, function):
    if not hasattr(llm, "start_trace"):
        return function(), []
    llm.start_trace()
    try:
        result = function()
    finally:
        calls = llm.end_trace()
    return result, calls


def summarize_trace(calls):
    requests = []
    for call in calls:
        requests.extend(call.get("attempt_history", []))
        if not call.get("error"):
            requests.append(call)

    summary = {
        "call_count": len(calls),
        "provider_request_count": len(requests),
        "retry_count": len(requests) - len(calls),
        "successful_calls": sum(not call.get("error") for call in calls),
        "failed_calls": sum(bool(call.get("error")) for call in calls),
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
    }
    for request in requests:
        for name, count in token_counts(request.get("usage")).items():
            summary[name] += count
    return summary
