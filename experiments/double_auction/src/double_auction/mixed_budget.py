"""Persistent, conservative request accounting for mixed-model auctions only."""

import json
import math
import os
import threading
import uuid

from experiment_runtime.models import model_catalog

from .providers import TracedLLM

# USD per million tokens, checked 2026-09-20. Ignore caching discounts.
RATES = {name: (spec["input_price"], spec["output_price"])
         for name, spec in model_catalog("mixed").items()}


class BudgetLimit(RuntimeError):
    pass


class BudgetLedger:
    """Reserve before transport; unknown usage remains charged on resume.

    The caller holds the study's process lock. This lock additionally serializes
    worker accounting. This is an application limit at the frozen rates, not a
    provider-side invoice guarantee against unannounced billing changes.
    """

    def __init__(self, path, limit):
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError("Budget must be positive and finite")
        self.path, self.limit = path, limit
        self.lock = threading.Lock()
        self.charges = {}
        self.settled = set()
        self.blocked = False
        if path.exists():
            for line in path.read_text().splitlines():
                event = json.loads(line)
                self._apply(event)
        if self.total > limit or self.blocked:
            raise BudgetLimit("Recorded costs already exceed this budget")

    @property
    def total(self):
        return sum(self.charges.values())

    def _apply(self, event):
        key, amount = event["id"], event["amount_usd"]
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("Invalid budget ledger amount")
        if event["event"] == "reserve":
            if key in self.charges:
                raise ValueError("Duplicate budget reservation")
        elif event["event"] == "settle":
            if key not in self.charges or key in self.settled:
                raise ValueError("Invalid budget settlement")
            self.settled.add(key)
            if amount > self.charges[key]:
                self.blocked = True
        else:
            raise ValueError("Unknown budget event")
        self.charges[key] = amount

    def _record(self, event):
        with self.path.open("a") as stream:
            stream.write(json.dumps(event) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._apply(event)

    def reserve(self, amount, model):
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("Invalid reservation")
        with self.lock:
            if self.blocked or self.total + amount > self.limit:
                raise BudgetLimit(f"Budget pause: ${self.total:.4f} accounted; "
                                  f"next request needs ${amount:.4f}; cap ${self.limit:.2f}")
            key = uuid.uuid4().hex
            self._record({"event": "reserve", "id": key,
                          "amount_usd": amount, "model": model})
            return key

    def settle(self, key, amount):
        with self.lock:
            if key not in self.charges or key in self.settled:
                raise ValueError("Unknown or already settled reservation")
            if not math.isfinite(amount) or amount < 0:
                raise ValueError("Invalid settled cost")
            reserved = self.charges[key]
            self._record({"event": "settle", "id": key, "amount_usd": amount})
            if amount > reserved:
                raise BudgetLimit("Provider cost exceeded reserved allowance; inspect pricing before resuming")


class BudgetedLLM(TracedLLM):
    """Preserve the existing request/trace/retry behavior; gate every attempt."""

    def __init__(self, spec, plan, ledger):
        super().__init__(spec["provider"], spec["model"], plan["reasoning_effort"],
                         plan["max_tokens"], spec["backend"])
        self.ledger = ledger
        self.rates = plan["price_per_million_tokens"][spec["preset"]]

    def complete(self, request, **kwargs):
        # Prompts are text-only. UTF-8 bytes plus 1,024 framing tokens conservatively
        # bound input; reserve twice the requested output cap for billing variation.
        input_bound = len(json.dumps(request, ensure_ascii=False).encode("utf-8")) + 1024
        output_bound = 2 * self.max_tokens
        reserved = (input_bound * self.rates[0] + output_bound * self.rates[1]) / 1e6
        key = self.ledger.reserve(reserved, self.model)
        completion = super().complete(request, **kwargs)
        usage = completion.usage
        # Transport errors, missing or malformed usage leave the full reservation.
        counts = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
        if all(isinstance(n, int) and n >= 0 for n in counts):
            cost = sum(n * rate for n, rate in zip(counts, self.rates)) / 1e6
            reported = usage.get("cost")
            if reported is not None:
                if not isinstance(reported, (int, float)) or not math.isfinite(reported) or reported < 0:
                    raise ValueError("Invalid provider cost; full reservation retained")
                cost = max(cost, reported)
            self.ledger.settle(key, cost)
        return completion
