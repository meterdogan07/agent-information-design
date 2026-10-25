"""Prompt, parser, state machine, and deterministic economic outcomes.

Nothing in this file calls an API or writes a result.  Given a market, signal
assignment, and a callable model, ``run_episode`` is deterministic conditional
on the model's raw responses.
"""

from __future__ import annotations

from experiment_runtime.models import generation_settings

import copy
import json
import math
import re
from typing import Any, Callable

from . import design

GENERATION = generation_settings("bargaining")


OBJECTIVE = (
    "Maximize your own expected monetary payoff. No trade gives you payoff 0. "
    "There is no additional reward for agreement, equality, fairness, or the "
    "other agent's payoff. Use only the information shown in this prompt."
)
BACKGROUND = (
    "Seller cost and buyer value are independently and uniformly drawn from "
    "20 to 100. This prior is identical in every information condition. "
    "Knowing your own reservation value gives no additional information about "
    "the other agent's reservation value."
)

ACTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "single_good_bargaining_action_v6",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": []},
                "price": {"type": ["number", "null"]},
                "message": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["decision", "price", "message", "reasoning"],
            "additionalProperties": False,
        },
    },
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def action_schema(decisions: tuple[str, ...]) -> dict[str, Any]:
    value = copy.deepcopy(ACTION_SCHEMA)
    value["json_schema"]["schema"]["properties"]["decision"]["enum"] = list(
        decisions
    )
    return value


def legal_decisions(
    actor: str,
    standing_offer: dict[str, Any] | None,
    turn: int,
    horizon: int,
) -> tuple[str, ...]:
    can_accept = bool(
        standing_offer is not None and standing_offer["proposer"] != actor
    )
    if turn == horizon:
        return ("accept", "reject") if can_accept else ("reject",)
    return ("propose", "accept", "reject") if can_accept else (
        "propose", "reject"
    )


def _json_objects(raw: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    objects = []
    for index, character in enumerate(raw):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects


def parse_action(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse exactly one action from the complete, unmodified response."""
    objects = _json_objects(raw)
    if len(objects) != 1:
        return None, "expected exactly one JSON object"
    value = objects[0]
    required = {"decision", "price", "message", "reasoning"}
    if set(value) != required:
        return None, "action fields differ from the schema"
    decision = value["decision"]
    if not isinstance(decision, str):
        return None, "decision is not a string"
    decision = decision.strip().lower()
    if decision not in {"propose", "accept", "reject"}:
        return None, "unknown decision"
    price = value["price"]
    if price is not None:
        if isinstance(price, bool):
            return None, "price is not numeric"
        try:
            price = float(price)
        except (TypeError, ValueError):
            return None, "price is not numeric"
        if not math.isfinite(price):
            return None, "price is not finite"
    message = value["message"]
    reasoning = value["reasoning"]
    if not isinstance(message, str) or not isinstance(reasoning, str):
        return None, "message and reasoning must be strings"
    if len(message) > design.MAX_PUBLIC_MESSAGE_CHARACTERS:
        return None, "public message exceeds its limit"
    if len(reasoning) > design.MAX_PRIVATE_REASONING_CHARACTERS:
        return None, "private reasoning exceeds its limit"
    return {
        "decision": decision,
        "price": price,
        "message": message,
        "reasoning": reasoning,
    }, None


def validate_action(
    action: dict[str, Any] | None,
    actor: str,
    standing_offer: dict[str, Any] | None,
    decisions: tuple[str, ...],
) -> tuple[bool, str | None]:
    if action is None:
        return False, "parse_error"
    decision = action["decision"]
    if decision not in decisions:
        return False, "action_not_legal_in_current_state"
    if decision == "propose":
        price = action["price"]
        if price is None or not design.PRICE_MIN <= price <= design.PRICE_MAX:
            return False, "proposal_price_out_of_bounds"
        return True, None
    if action["price"] is not None:
        return False, "nonproposal_price_must_be_null"
    if decision == "accept" and (
        standing_offer is None or standing_offer["proposer"] == actor
    ):
        return False, "no_counterpart_offer_to_accept"
    return True, None


def signal_card(signal: str, market: dict[str, Any]) -> str:
    if signal == "hidden":
        return "SIGNAL: the counterpart's exact reservation value is hidden."
    if signal == "coarse":
        fact = "exists" if market["buyer_value"] > market["seller_cost"] else "does not exist"
        return (
            f"SIGNAL: a verifier confirms that a mutually profitable price {fact}. "
            "The counterpart's exact reservation value remains hidden."
        )
    if signal == "exact":
        return (
            f"SIGNAL: seller cost = {market['seller_cost']}; "
            f"buyer value = {market['buyer_value']}."
        )
    raise ValueError("signal must be hidden, coarse, or exact")


def role_card(actor: str, market: dict[str, Any]) -> str:
    if actor == "seller":
        cost = market["seller_cost"]
        return (
            f"You are the SELLER. Your private cost is {cost}. If trade occurs "
            f"at price p, your payoff is p - {cost}."
        )
    if actor == "buyer":
        value = market["buyer_value"]
        return (
            f"You are the BUYER. Your private value is {value}. If trade occurs "
            f"at price p, your payoff is {value} - p."
        )
    raise ValueError("actor must be seller or buyer")


def format_price(value: float | None) -> str:
    return "none" if value is None else f"{float(value):.6g}"


def standing_offer_card(standing: dict[str, Any] | None) -> str:
    if standing is None:
        return "STANDING OFFER: none."
    return (
        "STANDING OFFER (authoritative): "
        f"proposal_id={standing['proposal_id']}; proposer={standing['proposer']}; "
        f"price={format_price(standing['price'])}."
    )


def public_history(events: list[dict[str, Any]]) -> str:
    if not events:
        return "(none)"
    lines = []
    for event in events:
        fields = [
            f"turn={event['turn']}",
            f"actor={event['actor']}",
            f"decision={event['decision']}",
            f"valid={str(bool(event['valid'])).lower()}",
        ]
        if event.get("proposal_id"):
            fields.append(f"proposal_id={event['proposal_id']}")
        if event.get("price") is not None:
            fields.append(f"price={format_price(event['price'])}")
        if event.get("accepted_proposal_id"):
            fields.append(f"accepted_proposal_id={event['accepted_proposal_id']}")
        fields.append("public_message=" + json.dumps(event.get("message", "")))
        lines.append(" | ".join(fields))
    return "\n".join(lines)


def build_prompt(
    signal: str,
    actor: str,
    market: dict[str, Any],
    events: list[dict[str, Any]],
    standing: dict[str, Any] | None,
    turn: int,
    horizon: int,
) -> str:
    decisions = legal_decisions(actor, standing, turn, horizon)
    can_accept = "accept" in decisions
    remaining_count = horizon - turn
    remaining = (
        "No messages remain after this one."
        if remaining_count == 0
        else f"{remaining_count} message{'s' if remaining_count != 1 else ''} remain after this one."
    )
    if turn == horizon and can_accept:
        legal = (
            "- ACCEPT: accept the standing offer; use price=null.\n"
            "- REJECT: end without trade; use price=null.\n"
            "PROPOSE is illegal because nobody could answer it."
        )
    elif turn == horizon:
        legal = (
            "- REJECT: end without trade; use price=null.\n"
            "There is no offer to accept and nobody could answer a proposal."
        )
    elif can_accept:
        legal = (
            "- PROPOSE: give a numeric price; it replaces the standing offer.\n"
            "- ACCEPT: accept the standing offer; use price=null.\n"
            "- REJECT: end immediately without trade; use price=null."
        )
    else:
        legal = (
            "- PROPOSE: give a numeric price; it becomes the standing offer.\n"
            "- REJECT: end immediately without trade; use price=null.\n"
            "There is no counterpart offer to accept."
        )
    return f"""GAME
You and one other agent are bargaining over the sale of one indivisible item.
The seller owns the item and incurs its private cost only if it is sold. The
buyer receives its private value only if it is purchased.

You alternate messages. There is only one standing offer. A new proposal
replaces the previous one. ACCEPT executes the displayed standing offer.
REJECT ends negotiation. If no offer is accepted by the limit, no trade occurs
and both payoffs are 0. A proposal is legal only if a later response remains.

YOUR ROLE AND PRIVATE INFORMATION
{role_card(actor, market)}

BACKGROUND UNCERTAINTY
{BACKGROUND}

INFORMATION AVAILABLE TO YOU
{signal_card(signal, market)}

YOUR OBJECTIVE
{OBJECTIVE}

CURRENT STATE
This is message {turn} of {horizon}. {remaining}
{standing_offer_card(standing)}

PUBLIC DIALOGUE SO FAR
{public_history(events)}

LEGAL ACTIONS
{legal}

Your public message is shown in full to the other agent. Keep private reasoning
out of the public message.

Return exactly one JSON object:
{{"decision":"{'|'.join(decisions)}","price":<number or null>,"message":"public text","reasoning":"brief private rationale"}}"""


def _call_model(
    model: Callable[..., str],
    prompt: str,
    actor: str,
    turn: int,
    max_tokens: int,
    decisions: tuple[str, ...],
) -> dict[str, Any]:
    if len(prompt) > design.MAX_PROMPT_CHARACTERS:
        raise RuntimeError(
            f"prompt has {len(prompt)} characters; limit is "
            f"{design.MAX_PROMPT_CHARACTERS}; nothing was truncated"
        )
    schema = action_schema(decisions)
    response = model(
        prompt,
        temperature=None,
        max_tokens=max_tokens,
        response_format=schema,
    )
    action, parse_error = parse_action(response)
    return {
        "actor": actor,
        "turn": turn,
        "prompt": prompt,
        "response": response,
        "response_format": schema,
        "parsed_action": action,
        "parse_error": parse_error,
    }


def actor_order(first_mover: str, horizon: int) -> list[str]:
    second = "buyer" if first_mover == "seller" else "seller"
    return [first_mover if index % 2 == 0 else second for index in range(horizon)]


def run_episode_with_signals(
    market: dict[str, Any],
    condition: str,
    signals: dict[str, str],
    model: Callable[..., str],
    horizon: int,
    max_tokens: int = GENERATION["max_tokens"],
    protocol_version: str = design.PROTOCOL_VERSION,
    prompt_version: str = design.PROMPT_VERSION,
) -> dict[str, Any]:
    design.validate_market(market)
    if horizon not in design.HORIZONS:
        raise ValueError("unsupported horizon")
    if set(signals) != {"seller", "buyer"}:
        raise ValueError("signals must specify seller and buyer")
    if any(value not in {"hidden", "coarse", "exact"} for value in signals.values()):
        raise ValueError("unknown signal")
    events: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    standing = None
    termination = "horizon"
    accepted_price = None
    accepted_id = None
    accepted_turn = None
    behavioral_agreement = False
    accepted_ir = False

    for turn, actor in enumerate(actor_order(market["first_mover"], horizon), 1):
        decisions = legal_decisions(actor, standing, turn, horizon)
        prompt = build_prompt(
            signals[actor], actor, market, events, standing, turn, horizon
        )
        call = _call_model(model, prompt, actor, turn, max_tokens, decisions)
        valid, error = validate_action(
            call["parsed_action"], actor, standing, decisions
        )
        call.update(legal=valid, validation_error=error)
        action = call["parsed_action"] or {
            "decision": "invalid", "price": None, "message": "", "reasoning": ""
        }
        event = {
            "turn": turn,
            "actor": actor,
            "decision": action["decision"],
            "valid": valid,
            "validation_error": error,
            "price": action["price"],
            "proposal_id": None,
            "accepted_proposal_id": None,
            "message": action["message"],
        }
        if valid and action["decision"] == "propose":
            proposal_id = f"{market['market_id']}-T{turn}"
            standing = {
                "proposal_id": proposal_id,
                "proposer": actor,
                "price": action["price"],
                "turn": turn,
            }
            event["proposal_id"] = proposal_id
        elif valid and action["decision"] == "accept":
            assert standing is not None
            behavioral_agreement = True
            accepted_price = float(standing["price"])
            accepted_id = standing["proposal_id"]
            accepted_turn = turn
            accepted_ir = bool(
                market["seller_cost"] <= accepted_price <= market["buyer_value"]
            )
            event["accepted_proposal_id"] = accepted_id
            termination = "accepted_ir" if accepted_ir else "accepted_non_ir"
        elif valid and action["decision"] == "reject":
            termination = "rejected"
        calls.append(call)
        events.append(event)
        if valid and action["decision"] in {"accept", "reject"}:
            break

    outcome = {
        "events": events,
        "calls": calls,
        "termination": termination,
        "standing_offer": standing,
        "behavioral_agreement": behavioral_agreement,
        "accepted_price": accepted_price,
        "accepted_proposal_id": accepted_id,
        "accepted_turn": accepted_turn,
        "accepted_ir": accepted_ir,
        "executed_trade": bool(behavioral_agreement and accepted_ir),
    }
    return {
        "protocol_version": protocol_version,
        "prompt_version": prompt_version,
        "condition": condition,
        "signal_by_actor": signals,
        "market": market,
        "outcome": outcome,
        "metrics": evaluate(market, outcome),
    }


def run_episode(
    market: dict[str, Any],
    condition: str,
    model: Callable[..., str],
    horizon: int,
    max_tokens: int = GENERATION["max_tokens"],
) -> dict[str, Any]:
    return run_episode_with_signals(
        market=market,
        condition=condition,
        signals=design.signals_for(condition),
        model=model,
        horizon=horizon,
        max_tokens=max_tokens,
    )


def _position(price: float | None, market: dict[str, Any]) -> float | None:
    surplus = market["buyer_value"] - market["seller_cost"]
    if price is None or surplus <= 0:
        return None
    return (price - market["seller_cost"]) / surplus


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _is_endpoint(price: float | None, market: dict[str, Any]) -> bool:
    if price is None:
        return False
    return bool(
        math.isclose(
            price, market["seller_cost"], rel_tol=0.0,
            abs_tol=design.PRICE_EPSILON,
        )
        or math.isclose(
            price, market["buyer_value"], rel_tol=0.0,
            abs_tol=design.PRICE_EPSILON,
        )
    )


def evaluate(market: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    """Recompute every economic and process outcome from authoritative state."""
    design.validate_market(market)
    potential = max(0.0, market["buyer_value"] - market["seller_cost"])
    behavioral = bool(outcome.get("behavioral_agreement"))
    price = outcome.get("accepted_price")
    actual_ir = bool(
        behavioral
        and price is not None
        and market["seller_cost"] <= price <= market["buyer_value"]
    )
    if bool(outcome.get("executed_trade")) != actual_ir:
        raise ValueError("execution differs from deterministic IR recomputation")
    welfare = potential if actual_ir else 0.0
    seller_gain = price - market["seller_cost"] if actual_ir else 0.0
    buyer_gain = market["buyer_value"] - price if actual_ir else 0.0
    seller_share = seller_gain / potential if actual_ir and potential else None
    proposals = [
        event for event in outcome.get("events", [])
        if event.get("valid") and event.get("decision") == "propose"
    ]
    ir_proposals = [
        event for event in proposals
        if market["seller_cost"] <= event["price"] <= market["buyer_value"]
    ]
    strict_ir_proposals = [
        event for event in proposals
        if (
            market["seller_cost"] + design.PRICE_EPSILON
            < event["price"]
            < market["buyer_value"] - design.PRICE_EPSILON
        )
    ]
    endpoint_proposals = [
        event for event in proposals
        if potential and _is_endpoint(event["price"], market)
    ]
    accepted_at_endpoint = bool(actual_ir and _is_endpoint(price, market))
    positions = {"seller": [], "buyer": []}
    for event in proposals:
        position = _position(event["price"], market)
        if position is not None:
            positions[event["actor"]].append(position)
    return {
        "potential_surplus": potential,
        "behavioral_agreement": behavioral,
        "weak_ir_execution": actual_ir,
        "unsafe_acceptance": bool(behavioral and not actual_ir),
        "welfare": welfare,
        "normalized_welfare": welfare / potential if potential else 1.0,
        "decision_quality": float(actual_ir) if potential else float(not behavioral),
        "seller_gain": seller_gain,
        "buyer_gain": buyer_gain,
        "seller_surplus_share": seller_share,
        "buyer_surplus_share": 1 - seller_share if seller_share is not None else None,
        "split_inequality": abs(2 * seller_share - 1) if seller_share is not None else None,
        "accepted_price_position": _position(price, market),
        "proposal_count": len(proposals),
        "ir_proposal_count": len(ir_proposals),
        "strict_ir_proposal_count": len(strict_ir_proposals),
        "endpoint_proposal_count": len(endpoint_proposals),
        "ever_ir_offer": bool(ir_proposals),
        "ever_strict_ir_offer": bool(strict_ir_proposals),
        "ever_endpoint_offer": bool(endpoint_proposals),
        "failed_after_ir_offer": bool(potential and not actual_ir and ir_proposals),
        "failed_after_strict_ir_offer": bool(
            potential and not actual_ir and strict_ir_proposals
        ),
        "failed_after_endpoint_offer": bool(
            potential and not actual_ir and endpoint_proposals
        ),
        "accepted_at_endpoint": accepted_at_endpoint,
        "accepted_strict_ir": bool(actual_ir and not accepted_at_endpoint),
        "positive_market_missed_trade": bool(potential and not actual_ir),
        "seller_first_offer_position": positions["seller"][0] if positions["seller"] else None,
        "buyer_first_offer_position": positions["buyer"][0] if positions["buyer"] else None,
        "seller_concession": (
            positions["seller"][0] - positions["seller"][-1]
            if len(positions["seller"]) >= 2 else None
        ),
        "buyer_concession": (
            positions["buyer"][-1] - positions["buyer"][0]
            if len(positions["buyer"]) >= 2 else None
        ),
        "turns_used": len(outcome.get("calls", [])),
        "agreement_turn": outcome.get("accepted_turn"),
        "termination": outcome.get("termination"),
        "invalid_action_count": sum(
            not call.get("legal") for call in outcome.get("calls", [])
        ),
        "parse_error_count": sum(
            bool(call.get("parse_error")) for call in outcome.get("calls", [])
        ),
        "public_message_characters": sum(
            len(str((call.get("parsed_action") or {}).get("message", "")))
            for call in outcome.get("calls", [])
        ),
    }


class ReplayModel:
    """Replays raw responses while the engine reconstructs every prompt."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)

    def __call__(self, _prompt: str, **_kwargs: Any) -> str:
        if not self.responses:
            raise RuntimeError("replay exhausted")
        return self.responses.pop(0)


def make_mock_model() -> Callable[..., str]:
    """Deterministic offline policy used only for protocol tests."""

    def response(decision: str, price: float | None, message: str) -> str:
        return canonical_json({
            "decision": decision,
            "price": price,
            "message": message,
            "reasoning": "deterministic offline check",
        })

    def mock(prompt: str, **kwargs: Any) -> str:
        decisions = tuple(
            kwargs["response_format"]["json_schema"]["schema"]
            ["properties"]["decision"]["enum"]
        )
        actor = "seller" if "You are the SELLER" in prompt else "buyer"
        number = r"([0-9]+(?:\.[0-9]+)?)"
        own_match = re.search(r"private (?:cost|value) is " + number, prompt)
        assert own_match
        own = float(own_match.group(1))
        standing = re.search(r"proposer=([^;]+); price=" + number, prompt)
        if standing and "accept" in decisions:
            price = float(standing.group(2))
            acceptable = price >= own if actor == "seller" else price <= own
            if acceptable:
                return response("accept", None, "Accepted.")
        if "propose" in decisions:
            price = own + 10 if actor == "seller" else max(design.PRICE_MIN, own - 10)
            return response("propose", price, "My offer.")
        return response("reject", None, "No agreement.")

    return mock
