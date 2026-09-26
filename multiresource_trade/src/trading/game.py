"""Alternating-offer bargaining rules and welfare scoring."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Mapping

from .. import (
    ACTION_INTERFACE_VERSION,
    ACTION_INTERFACE_VERSIONS,
    LEGACY_ACTION_INTERFACE_VERSION,
    PROTOCOL_VERSION,
)
from .markets import RESOURCES, first_best_gain, gains
from .p2p_prompts import AGENT_POLICIES, CONDITIONS, build_prompt, prompt_version


ACTIONS = ("propose", "accept", "wait")
MODEL_FIELDS = {"action", "you_give", "you_receive", "message"}
LEGACY_MODEL_FIELDS = MODEL_FIELDS | {"proposal_id"}
GLOBAL_TRANSFER_FIELDS = {"a_gives", "b_gives"}


def action_schema(
    resources: tuple[str, ...] = RESOURCES,
    action_interface: str = ACTION_INTERFACE_VERSION,
) -> dict[str, Any]:
    """JSON schema sent to the model."""
    if action_interface not in ACTION_INTERFACE_VERSIONS:
        raise ValueError(f"unknown action interface: {action_interface}")
    quantity = {"type": "integer", "minimum": 0}
    bundle = {
        "type": "object",
        "properties": {item: quantity for item in resources},
        # Strict OpenAI schemas require every declared resource field. A zero
        # means the resource is absent and is canonicalized away after parsing.
        "required": list(resources),
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "trade_action",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "you_give": bundle,
                    "you_receive": bundle,
                    "message": {"type": "string"},
                } | (
                    {"proposal_id": {"type": "string"}}
                    if action_interface == LEGACY_ACTION_INTERFACE_VERSION
                    else {}
                ),
                "required": sorted(
                    LEGACY_MODEL_FIELDS
                    if action_interface == LEGACY_ACTION_INTERFACE_VERSION
                    else MODEL_FIELDS
                ),
                "additionalProperties": False,
            },
        },
    }


def parse_action(
    raw: str,
    resources: tuple[str, ...] = RESOURCES,
    actor: str = "A",
    action_interface: str = ACTION_INTERFACE_VERSION,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse a model action into the internal A/B transfer representation.

    The public model interface is actor-relative (``you_give`` and
    ``you_receive``). A narrow compatibility layer also accepts the former
    global A/B fields and a single ``transfer`` wrapper. It only normalizes
    syntax whose meaning is unambiguous; bargaining-rule and feasibility
    errors remain invalid in :func:`validate_action`.
    """
    if actor not in {"A", "B"}:
        raise ValueError("actor must be A or B")
    if action_interface not in ACTION_INTERFACE_VERSIONS:
        raise ValueError(f"unknown action interface: {action_interface}")
    model_fields = (
        LEGACY_MODEL_FIELDS
        if action_interface == LEGACY_ACTION_INTERFACE_VERSION
        else MODEL_FIELDS
    )
    if not isinstance(raw, str):
        return None, ["invalid_json"]
    stripped = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1)
    try:
        action = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return None, ["invalid_json"]
    if not isinstance(action, dict):
        return None, ["wrong_fields"]

    # Some endpoints nest the transfer even when instructed to use top-level
    # fields. Flatten exactly one recognized wrapper without interpreting prose.
    wrapper = action.pop("transfer", None)
    if wrapper is not None:
        if not isinstance(wrapper, dict):
            return None, ["transfer_must_be_object"]
        transfer_fields = (
            model_fields | GLOBAL_TRANSFER_FIELDS
        ) - {"action", "proposal_id", "message"}
        if any(field in action for field in transfer_fields):
            return None, ["ambiguous_transfer_fields"]
        action.update(wrapper)

    # Be liberal when parsing plain-text fallbacks from older providers: an
    # obsolete proposal_id is accepted syntactically but ignored by the new
    # interface. The strict schema and every current prompt omit this field.
    allowed_fields = LEGACY_MODEL_FIELDS | GLOBAL_TRANSFER_FIELDS | {"proposer"}
    if set(action) - allowed_fields or "action" not in action:
        return None, ["wrong_fields"]

    kind = action.get("action")
    proposal_id = action.get("proposal_id", "")
    message = action.get("message", "")
    if proposal_id is None and kind != "accept":
        proposal_id = ""
    if message is None:
        message = ""
    if action_interface != LEGACY_ACTION_INTERFACE_VERSION:
        proposal_id = ""

    relative_present = "you_give" in action or "you_receive" in action
    global_present = "a_gives" in action or "b_gives" in action
    if relative_present and global_present:
        return None, ["ambiguous_transfer_fields"]
    if kind == "propose" and not (relative_present or global_present):
        return None, ["proposal_missing_transfer"]

    if relative_present:
        you_give = action.get("you_give", {})
        you_receive = action.get("you_receive", {})
        if actor == "A":
            a_gives, b_gives = you_give, you_receive
        else:
            a_gives, b_gives = you_receive, you_give
    else:
        # Backward-compatible parsing for archived callers and providers that
        # still emit the old globally named fields.
        a_gives = action.get("a_gives", {})
        b_gives = action.get("b_gives", {})

    canonical = {
        "action": kind,
        "proposal_id": proposal_id,
        "a_gives": a_gives,
        "b_gives": b_gives,
        "message": message,
    }

    errors = []
    if canonical["action"] not in ACTIONS:
        errors.append("unknown_action")
    if not isinstance(canonical["proposal_id"], str):
        errors.append("proposal_id_must_be_string")
    if not isinstance(canonical["message"], str):
        errors.append("message_must_be_string")
    for side in ("a_gives", "b_gives"):
        bundle = canonical[side]
        if not isinstance(bundle, dict):
            errors.append(f"{side}_must_be_object")
            continue
        if set(bundle) - set(resources):
            errors.append(f"{side}_has_unknown_resource")
        for quantity in bundle.values():
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
                errors.append(f"{side}_quantities_must_be_nonnegative_integers")
                break
    return (canonical if not errors else None), errors


def validate_action(
    action: Mapping[str, Any],
    actor: str,
    market: Mapping[str, Any],
    active_offer: Mapping[str, Any] | None,
    turn: int,
    max_messages: int,
    proposals_made: int,
    proposal_limit: int,
    discovery_phase: bool = False,
    action_interface: str = ACTION_INTERFACE_VERSION,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Apply the bargaining rules and return a canonical action."""
    if action_interface not in ACTION_INTERFACE_VERSIONS:
        raise ValueError(f"unknown action interface: {action_interface}")
    uses_legacy_ids = action_interface == LEGACY_ACTION_INTERFACE_VERSION
    kind = action["action"]
    if discovery_phase and kind != "wait":
        return None, ["discovery_phase_only_allows_wait"]
    if kind == "propose":
        errors = []
        if turn == max_messages:
            errors.append("final_message_cannot_propose")
        if proposals_made >= proposal_limit:
            errors.append("proposal_limit_reached")
        if uses_legacy_ids and action["proposal_id"]:
            errors.append("new_offer_must_leave_proposal_id_empty")

        trade = {
            side: {item: quantity for item, quantity in action[side].items() if quantity > 0}
            for side in ("a_gives", "b_gives")
        }
        if not trade["a_gives"] or not trade["b_gives"]:
            errors.append("trade_must_move_resources_both_ways")
        if set(trade["a_gives"]) & set(trade["b_gives"]):
            errors.append("same_resource_cannot_move_both_ways")
        for side, inventory in (("a_gives", "inventory_a"), ("b_gives", "inventory_b")):
            for item, quantity in trade[side].items():
                if quantity > market[inventory][item]:
                    errors.append(f"{side}_exceeds_inventory")
        if errors:
            return None, errors
        return {
            "action": "propose",
            "proposal_id": f"P{turn}-{actor}",
            "proposer": actor,
            **trade,
            "message": action["message"],
        }, []

    errors = []
    echoed_trade = {
        side: {item: quantity for item, quantity in action[side].items() if quantity > 0}
        for side in ("a_gives", "b_gives")
    }
    has_positive_transfer = any(echoed_trade[side] for side in echoed_trade)
    if kind == "accept":
        if active_offer is None:
            errors.append("nothing_to_accept")
        elif active_offer["proposer"] == actor:
            errors.append("cannot_accept_own_offer")
        elif uses_legacy_ids and action["proposal_id"] != active_offer["proposal_id"]:
            errors.append("wrong_proposal_id")
        elif has_positive_transfer and any(
            echoed_trade[side] != active_offer[side]
            for side in ("a_gives", "b_gives")
        ):
            errors.append("accepted_terms_do_not_match_active_offer")
    elif has_positive_transfer:
        errors.append("only_proposals_contain_transfers")
    elif uses_legacy_ids and action["proposal_id"]:
        errors.append("proposal_id_only_used_for_acceptance")
    if errors:
        return None, errors
    return {
        "action": kind,
        "proposal_id": (
            active_offer["proposal_id"]
            if kind == "accept" and active_offer is not None
            else ""
        ),
        "a_gives": {},
        "b_gives": {},
        "message": action["message"],
    }, []


def score(market: Mapping[str, Any], agreement: Mapping[str, Any] | None) -> dict[str, Any]:
    """Apply the study's welfare rule while retaining both agents' raw gains."""
    gain_a, gain_b = gains(market, agreement)
    first_best = first_best_gain(market)
    # An accepted but non-beneficial allocation is still recorded as an
    # agreement, but it receives zero welfare under the preregistered rule.
    is_ir = agreement is not None and gain_a > 0 and gain_b > 0
    welfare = gain_a + gain_b if is_ir else 0
    return {
        "agreement": agreement is not None,
        "gain_a": gain_a,
        "gain_b": gain_b,
        "individually_rational": is_ir,
        "welfare": welfare,
        "normalized_welfare": welfare / first_best,
    }


def play_episode(
    episode_id: str,
    condition: str,
    market: Mapping[str, Any],
    max_messages: int,
    respond: Callable[[str], str],
    agent_policy: str = "plain_dialogue_self_interest",
    first_actor: str = "A",
    discovery_messages: int = 0,
    action_interface: str = ACTION_INTERFACE_VERSION,
) -> dict[str, Any]:
    """Run one alternating A/B negotiation."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")
    if agent_policy not in AGENT_POLICIES:
        raise ValueError(f"unknown agent policy: {agent_policy}")
    if max_messages < 2 or max_messages % 2:
        raise ValueError("max_messages must be an even integer of at least 2")
    if discovery_messages < 0 or discovery_messages % 2:
        raise ValueError("discovery_messages must be a nonnegative even integer")
    if first_actor not in {"A", "B"}:
        raise ValueError("first_actor must be A or B")
    if action_interface not in ACTION_INTERFACE_VERSIONS:
        raise ValueError(f"unknown action interface: {action_interface}")

    active_offer = None
    agreement = None
    termination = "message_limit"
    transcript = []
    proposal_limit = max(1, max_messages // 2 - 1)
    proposal_counts = {"A": 0, "B": 0}

    total_messages = discovery_messages + max_messages
    for turn in range(1, total_messages + 1):
        actor = first_actor if turn % 2 else ("B" if first_actor == "A" else "A")
        prompt = build_prompt(
            condition,
            actor,
            market,
            transcript,
            active_offer,
            turn,
            max_messages,
            proposal_counts[actor],
            proposal_limit,
            agent_policy,
            first_actor,
            discovery_messages,
            action_interface,
        )
        raw = respond(prompt)
        resources = tuple(market["inventory_a"])
        parsed, errors = parse_action(raw, resources, actor, action_interface)
        action = None
        if parsed is not None:
            action, rule_errors = validate_action(
                parsed,
                actor,
                market,
                active_offer,
                turn,
                total_messages,
                proposal_counts[actor],
                proposal_limit,
                turn <= discovery_messages,
                action_interface,
            )
            errors += rule_errors

        transcript.append({
            "turn": turn,
            "actor": actor,
            "prompt": prompt,
            "raw_response": raw,
            "action": action,
            "errors": errors,
        })
        if action is None:
            continue
        if action["action"] == "propose":
            # There is only one live offer; a counteroffer replaces it.
            active_offer = action
            proposal_counts[actor] += 1
        elif action["action"] == "wait":
            # Waiting while an offer is active is an explicit rejection.
            active_offer = None
        elif action["action"] == "accept":
            agreement = active_offer
            termination = "accepted"
            break

    return {
        "protocol_version": PROTOCOL_VERSION,
        "action_interface_version": action_interface,
        "prompt_version": prompt_version(agent_policy),
        "episode_id": episode_id,
        "condition": condition,
        "agent_policy": agent_policy,
        "first_actor": first_actor,
        "max_messages": max_messages,
        "discovery_messages": discovery_messages,
        "market": dict(market),
        "transcript": transcript,
        "agreement": agreement,
        "termination": termination,
        "outcome": score(market, agreement),
    }
