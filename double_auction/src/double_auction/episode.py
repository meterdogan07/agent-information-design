"""One market episode: feedback, revision rounds, and outcome summaries."""

from __future__ import annotations

import re

from .analysis import order_diagnostics
from .market import (
    TEMPERATURE_POLICY,
    clear_market,
    parse_number,
    validate_orders, protocol_version,
)
from .prompts import build_prompt
from .providers import run_traced, summarize_trace


def feedback_message(policy, bids, asks, values, costs, mechanism="midpoint"):
    """Tell each agent only whether its provisional order would execute."""
    if policy == "none":
        return "No feedback was provided."
    if policy != "private_status":
        raise ValueError(f"unknown feedback policy {policy}")
    provisional = clear_market(values, costs, bids, asks, mechanism)
    trading_agents = {
        agent
        for trade in provisional["trades"]
        for agent in (trade["buyer"], trade["seller"])
    }
    feedback = {}
    for agent in list(values) + list(costs):
        if (agent in bids and bids[agent] is None) or (
                agent in asks and asks[agent] is None):
            feedback[agent] = (
                "Your previous order was invalid. You are not shown any other "
                "order, identity, value, or cost."
            )
        else:
            status = "would trade" if agent in trading_agents else "would not trade"
            feedback[agent] = (
                f"Your previous order {status}. You are not shown any other "
                "order, identity, value, or cost."
            )
    return feedback


def _collect_side_orders(llm, condition, inst, max_tokens,
                         round_number, rounds, feedback_policy, role,
                         agents, previous_orders, feedback):
    """Ask every agent on one side of the market for an order."""
    order_name = "bid" if role == "buyer" else "ask"
    orders, log = {}, []
    for agent in agents:
        agent_feedback = (
            feedback.get(agent) if isinstance(feedback, dict) else feedback
        )
        prompt = build_prompt(
            condition, inst, agent, role, round_number, rounds,
            feedback_policy, previous_orders.get(agent), agent_feedback,
        )
        response = llm(prompt, max_tokens=max_tokens)
        orders[agent] = parse_number(response, order_name)
        log.append({
            "round": round_number,
            "who": agent,
            "role": role,
            "prompt": prompt,
            "response": response,
            order_name: orders[agent],
        })
    return orders, log


def run_orders_round(llm, condition, inst, max_tokens,
                     round_number, rounds, feedback_policy, previous_bids,
                     previous_asks, feedback):
    bids, buyer_log = _collect_side_orders(
        llm, condition, inst, max_tokens, round_number, rounds,
        feedback_policy, "buyer", inst["buyers"], previous_bids, feedback,
    )
    asks, seller_log = _collect_side_orders(
        llm, condition, inst, max_tokens, round_number, rounds,
        feedback_policy, "seller", inst["sellers"], previous_asks, feedback,
    )
    log = buyer_log + seller_log
    return bids, asks, log


def run_orders(llm, condition, inst, max_tokens, rounds,
               feedback_policy):
    mechanism = inst.get("mechanism", "midpoint")
    previous_bids = {buyer: None for buyer in inst["buyers"]}
    previous_asks = {seller: None for seller in inst["sellers"]}
    feedback = None
    log, history = [], []
    for round_number in range(1, rounds + 1):
        bids, asks, round_log = run_orders_round(
            llm, condition, inst, max_tokens, round_number, rounds,
            feedback_policy, previous_bids, previous_asks, feedback,
        )
        valid_bids, valid_asks, invalid = validate_orders(
            inst["values"], inst["costs"], bids, asks,
        )
        provisional = clear_market(
            inst["values"], inst["costs"], valid_bids, valid_asks, mechanism,
        )
        diagnostics = order_diagnostics(
            inst["values"], inst["costs"], valid_bids, valid_asks,
            inst["first_best_welfare"], len(inst["first_best_trades"]),
        )
        released = None
        if round_number < rounds:
            released = feedback_message(
                feedback_policy, valid_bids, valid_asks,
                inst["values"], inst["costs"], mechanism,
            )
        history.append({
            "round": round_number,
            "submitted_bids": bids,
            "submitted_asks": asks,
            "bids": valid_bids,
            "asks": valid_asks,
            "invalid_orders": invalid,
            "provisional_outcome": provisional,
            "diagnostics": diagnostics,
            "feedback_released": released,
        })
        log.extend(round_log)
        previous_bids, previous_asks = bids, asks
        feedback = released
    return bids, asks, log, history


def _mean_revision(history, side):
    field = "submitted_bids" if side == "buyer" else "submitted_asks"
    revisions = []
    for previous, current in zip(history, history[1:]):
        for agent, value in current[field].items():
            old = previous[field].get(agent)
            if value is not None and old is not None:
                revisions.append(abs(value - old))
    return (sum(revisions) / len(revisions)) if revisions else None


def run_one(condition, llm, inst, max_tokens=2048, rounds=1,
            feedback_policy="none"):
    (submitted_bids, submitted_asks, log, round_history), raw_calls = run_traced(
        llm, lambda: run_orders(
            llm, condition, inst, max_tokens, rounds,
            feedback_policy,
        ))
    usage = summarize_trace(raw_calls)

    bids, asks, invalid_orders = validate_orders(
        inst["values"], inst["costs"], submitted_bids, submitted_asks)
    mechanism = inst.get("mechanism", "midpoint")
    outcome = clear_market(inst["values"], inst["costs"], bids, asks, mechanism)
    fb = inst["first_best_welfare"]
    diagnostics = order_diagnostics(
        inst["values"], inst["costs"], bids, asks, fb,
        len(inst["first_best_trades"]),
    )
    shading = [inst["values"][b] - v for b, v in submitted_bids.items()
               if v is not None]
    markup = [v - inst["costs"][s] for s, v in submitted_asks.items()
              if v is not None]
    return {
        **inst,
        "protocol_version": protocol_version(inst.get("market_design", "three_family")),
        "condition": condition,
        "mechanism": mechanism,
        "rounds": rounds,
        "feedback_policy": feedback_policy,
        "temperature": TEMPERATURE_POLICY,
        "max_tokens": max_tokens,
        "submitted_bids": submitted_bids,
        "submitted_asks": submitted_asks,
        "bids": bids,
        "asks": asks,
        "invalid_orders": invalid_orders,
        "log": log,
        "round_history": round_history,
        "raw_calls": raw_calls,
        "usage": usage,
        **outcome,
        **diagnostics,
        "implementation_loss": diagnostics["candidate_welfare"] - outcome["welfare"],
        "normalized_implementation_loss": (
            (diagnostics["candidate_welfare"] - outcome["welfare"]) / fb
            if fb > 0 else None
        ),
        "efficiency": outcome["welfare"] / fb,
        "trade_count": len(outcome["trades"]),
        "unparsed_orders": sum(
            v is None for v in list(submitted_bids.values()) + list(submitted_asks.values())
        ),
        "invalid_order_count": len(invalid_orders),
        "mean_buyer_shading": (sum(shading) / len(shading)) if shading else None,
        "mean_seller_markup": (sum(markup) / len(markup)) if markup else None,
        "mean_buyer_revision": _mean_revision(round_history, "buyer"),
        "mean_seller_revision": _mean_revision(round_history, "seller"),
        "all_round_invalid_orders": sum(
            len(round_row["invalid_orders"]) for round_row in round_history
        ),
    }

def make_mock_llm():
    """Offline stub used by tests and dry runs; it never calls a provider."""
    def llm(prompt, max_tokens=None, response_format=None):
        del max_tokens, response_format
        vm = re.search(r"item is worth ([0-9]+(?:\.[0-9]+)?)", prompt)
        cm = re.search(r"item costs you ([0-9]+(?:\.[0-9]+)?)", prompt)
        later = "Round 1 of" not in prompt and re.search(r"Round \d+ of", prompt)
        if vm:
            factor = 0.90 if later else 0.88
            return '{"reasoning":"safe buyer order","bid": %.1f}' % (factor * float(vm.group(1)))
        if cm:
            factor = 1.10 if later else 1.12
            return '{"reasoning":"safe seller order","ask": %.1f}' % (factor * float(cm.group(1)))
        return '{"reasoning":"no parse","bid": 0}'
    return llm
