"""One-shot, consulted, and feedback-mediated trade with private reviews."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Mapping

from .game import score
from .mediator_prompts import (
    build_consultation_prompt,
    build_consulted_mediator_prompt,
    build_feedback_review_prompt,
    build_final_review_prompt,
    build_mediator_prompt,
    build_private_review_prompt,
    build_revision_prompt,
)


MEDIATOR_PROTOCOL_VERSION = "open_proposal_private_review_mediator_20260820"
MEDIATOR_ACTION_INTERFACE_VERSION = "global_trade_and_private_review_20260820"
MEDIATOR_PROMPT_VERSION = "aligned_required_trial_proposal_no_leak_20260820"
MEDIATOR_CONDITIONS = ("inventory_only", "rank_only", "exact_values")
MEDIATOR_MODES = ("one_shot", "consultation", "feedback")

CONSULTED_MEDIATOR_PROTOCOL_VERSION = (
    "private_preproposal_consultation_mediator_20260823"
)
CONSULTED_MEDIATOR_ACTION_INTERFACE_VERSION = (
    "qualitative_consultation_global_trade_private_review_20260823"
)
CONSULTED_MEDIATOR_PROMPT_VERSION = (
    "quantity_aware_preproposal_reports_no_value_leak_20260823_r2"
)

FEEDBACK_MEDIATOR_PROTOCOL_VERSION = (
    "two_proposal_qualitative_feedback_mediator_20260823"
)
FEEDBACK_MEDIATOR_ACTION_INTERFACE_VERSION = (
    "global_trade_qualitative_feedback_private_review_20260823"
)
FEEDBACK_MEDIATOR_PROMPT_VERSION = (
    "bounded_qualitative_feedback_revision_20260823"
)


def proposal_schema(resources: tuple[str, ...]) -> dict[str, Any]:
    """Structured output for the mediator's unrestricted proposal."""
    quantity = {"type": "integer", "minimum": 0}
    bundle = {
        "type": "object",
        "properties": {item: quantity for item in resources},
        "required": list(resources),
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "mediated_trade_proposal",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["propose", "no_trade"],
                    },
                    "a_gives": bundle,
                    "b_gives": bundle,
                    "reason": {"type": "string"},
                },
                "required": ["action", "a_gives", "b_gives", "reason"],
                "additionalProperties": False,
            },
        },
    }


def review_schema() -> dict[str, Any]:
    """Structured output for a trader's private accept/reject decision."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "private_trade_review",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": ["accept", "reject"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["decision", "reason"],
                "additionalProperties": False,
            },
        },
    }


def feedback_schema() -> dict[str, Any]:
    """Structured qualitative feedback on the mediator's initial proposal."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "private_trade_feedback",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": ["accept", "reject"],
                    },
                    "requested_change": {
                        "type": "string",
                        "enum": [
                            "none",
                            "give_less",
                            "receive_more",
                            "different_bundle",
                        ],
                    },
                    "message": {"type": "string", "maxLength": 240},
                },
                "required": ["decision", "requested_change", "message"],
                "additionalProperties": False,
            },
        },
    }


def consultation_schema(resources: tuple[str, ...]) -> dict[str, Any]:
    """Structured preferred trade reported privately before mediation."""
    quantity = {"type": "integer", "minimum": 0}
    bundle = {
        "type": "object",
        "properties": {item: quantity for item in resources},
        "required": list(resources),
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "private_preproposal_consultation",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["seek_trade", "no_trade"],
                    },
                    "you_give": bundle,
                    "you_receive": bundle,
                    "flexibility": {
                        "type": "string",
                        "enum": [
                            "fixed",
                            "quantity_flexible",
                            "bundle_flexible",
                        ],
                    },
                    "message": {"type": "string", "maxLength": 240},
                },
                "required": [
                    "action", "you_give", "you_receive", "flexibility",
                    "message",
                ],
                "additionalProperties": False,
            },
        },
    }


def _json_object(raw: str) -> dict[str, Any] | None:
    """Parse one JSON object, tolerating only a surrounding JSON fence."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE
    )
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def parse_proposal(
    raw: str,
    resources: tuple[str, ...],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse and canonicalize a mediator proposal without repairing its terms."""
    value = _json_object(raw)
    if value is None:
        return None, ["invalid_json"]
    required = {"action", "a_gives", "b_gives", "reason"}
    if set(value) != required:
        return None, ["wrong_fields"]
    if value["action"] not in {"propose", "no_trade"}:
        return None, ["unknown_action"]
    if not isinstance(value["reason"], str):
        return None, ["reason_must_be_string"]

    errors = []
    bundles: dict[str, dict[str, int]] = {}
    for side in ("a_gives", "b_gives"):
        bundle = value[side]
        if not isinstance(bundle, dict):
            errors.append(f"{side}_must_be_object")
            continue
        if set(bundle) - set(resources):
            errors.append(f"{side}_has_unknown_resource")
        if any(
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity < 0
            for quantity in bundle.values()
        ):
            errors.append(f"{side}_quantities_must_be_nonnegative_integers")
        bundles[side] = {
            item: quantity
            for item, quantity in bundle.items()
            if isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0
        }
    if errors:
        return None, errors
    return {
        "action": value["action"],
        "a_gives": bundles["a_gives"],
        "b_gives": bundles["b_gives"],
        "reason": value["reason"],
    }, []


def validate_proposal(
    proposal: Mapping[str, Any],
    market: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Enforce no-trade consistency, positive exchange, and feasibility."""
    a_gives = dict(proposal["a_gives"])
    b_gives = dict(proposal["b_gives"])
    if proposal["action"] == "no_trade":
        if a_gives or b_gives:
            return None, ["no_trade_must_have_zero_quantities"]
        return {
            "action": "no_trade",
            "a_gives": {},
            "b_gives": {},
            "reason": proposal["reason"],
        }, []

    errors = []
    if not a_gives or not b_gives:
        errors.append("trade_must_move_resources_both_ways")
    if set(a_gives) & set(b_gives):
        errors.append("same_resource_cannot_move_both_ways")
    for side, inventory_key in (
        ("a_gives", "inventory_a"),
        ("b_gives", "inventory_b"),
    ):
        for item, quantity in proposal[side].items():
            if quantity > market[inventory_key].get(item, 0):
                errors.append(f"{side}_exceeds_inventory")
    if errors:
        return None, errors
    return {
        "action": "propose",
        "a_gives": a_gives,
        "b_gives": b_gives,
        "reason": proposal["reason"],
    }, []


def parse_review(raw: str) -> tuple[dict[str, str] | None, list[str]]:
    """Parse a private review; malformed output safely counts as rejection."""
    value = _json_object(raw)
    if value is None:
        return None, ["invalid_json"]
    if set(value) != {"decision", "reason"}:
        return None, ["wrong_fields"]
    if value["decision"] not in {"accept", "reject"}:
        return None, ["unknown_decision"]
    if not isinstance(value["reason"], str):
        return None, ["reason_must_be_string"]
    return {"decision": value["decision"], "reason": value["reason"]}, []


def parse_feedback(raw: str) -> tuple[dict[str, str] | None, list[str]]:
    """Parse bounded feedback; inconsistent or malformed output is invalid."""
    value = _json_object(raw)
    if value is None:
        return None, ["invalid_json"]
    if set(value) != {"decision", "requested_change", "message"}:
        return None, ["wrong_fields"]
    if value["decision"] not in {"accept", "reject"}:
        return None, ["unknown_decision"]
    if value["requested_change"] not in {
        "none", "give_less", "receive_more", "different_bundle"
    }:
        return None, ["unknown_requested_change"]
    if not isinstance(value["message"], str):
        return None, ["message_must_be_string"]
    if len(value["message"]) > 240:
        return None, ["message_too_long"]
    if value["decision"] == "accept" and value["requested_change"] != "none":
        return None, ["accept_requires_no_change"]
    if value["decision"] == "reject" and value["requested_change"] == "none":
        return None, ["reject_requires_change"]
    return {
        "decision": value["decision"],
        "requested_change": value["requested_change"],
        "message": value["message"],
    }, []


def parse_consultation(
    raw: str,
    resources: tuple[str, ...],
    actor: str,
    market: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse a feasible qualitative report without repairing its terms."""
    if actor not in {"A", "B"}:
        raise ValueError("actor must be A or B")
    value = _json_object(raw)
    if value is None:
        return None, ["invalid_json"]
    required = {"action", "you_give", "you_receive", "flexibility", "message"}
    if set(value) != required:
        return None, ["wrong_fields"]
    if value["action"] not in {"seek_trade", "no_trade"}:
        return None, ["unknown_action"]
    if value["flexibility"] not in {
        "fixed", "quantity_flexible", "bundle_flexible"
    }:
        return None, ["unknown_flexibility"]
    if not isinstance(value["message"], str):
        return None, ["message_must_be_string"]
    if len(value["message"]) > 240:
        return None, ["message_too_long"]
    if any(character.isdigit() for character in value["message"]):
        return None, ["message_must_not_contain_numbers"]

    errors = []
    bundles: dict[str, dict[str, int]] = {}
    for side in ("you_give", "you_receive"):
        bundle = value[side]
        if not isinstance(bundle, dict):
            errors.append(f"{side}_must_be_object")
            continue
        if set(bundle) != set(resources):
            errors.append(f"{side}_must_list_every_resource")
            continue
        if any(
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity < 0
            for quantity in bundle.values()
        ):
            errors.append(f"{side}_quantities_must_be_nonnegative_integers")
            continue
        bundles[side] = {
            item: quantity for item, quantity in bundle.items() if quantity > 0
        }
    if errors:
        return None, errors

    give = bundles["you_give"]
    receive = bundles["you_receive"]
    own_inventory = market[f"inventory_{actor.lower()}"]
    other = "b" if actor == "A" else "a"
    other_inventory = market[f"inventory_{other}"]
    if value["action"] == "no_trade":
        if give or receive:
            return None, ["no_trade_must_have_zero_quantities"]
    else:
        if not give or not receive:
            errors.append("trade_report_must_move_resources_both_ways")
        if set(give) & set(receive):
            errors.append("same_resource_cannot_move_both_ways")
        if any(quantity > own_inventory[item] for item, quantity in give.items()):
            errors.append("you_give_exceeds_inventory")
        if any(
            quantity > other_inventory[item] for item, quantity in receive.items()
        ):
            errors.append("you_receive_exceeds_other_inventory")
    if errors:
        return None, errors
    return {
        "action": value["action"],
        "you_give": give,
        "you_receive": receive,
        "flexibility": value["flexibility"],
        "message": value["message"],
    }, []


def play_mediator_episode(
    episode_id: str,
    condition: str,
    market: Mapping[str, Any],
    respond: Callable[[str, dict[str, Any]], str],
) -> dict[str, Any]:
    """Ask one mediator for an open proposal, then obtain two private reviews."""
    if condition not in MEDIATOR_CONDITIONS:
        raise ValueError(f"unknown mediator condition: {condition}")
    resources = tuple(market["inventory_a"])
    mediator_prompt = build_mediator_prompt(condition, market)
    mediator_raw = respond(mediator_prompt, proposal_schema(resources))
    parsed, errors = parse_proposal(mediator_raw, resources)
    proposal = None
    if parsed is not None:
        proposal, rule_errors = validate_proposal(parsed, market)
        errors += rule_errors

    reviews = []
    agreement = None
    proposed_trade = None
    if proposal is None:
        termination = "invalid_proposal"
    elif proposal["action"] == "no_trade":
        termination = "mediator_no_trade"
    else:
        proposed_trade = {
            "a_gives": proposal["a_gives"],
            "b_gives": proposal["b_gives"],
        }
        for actor in ("A", "B"):
            prompt = build_private_review_prompt(actor, market, proposal)
            raw = respond(prompt, review_schema())
            review, review_errors = parse_review(raw)
            reviews.append({
                "actor": actor,
                "prompt": prompt,
                "raw_response": raw,
                "review": review,
                "errors": review_errors,
            })
        if all(
            row["review"] is not None
            and row["review"]["decision"] == "accept"
            for row in reviews
        ):
            agreement = proposed_trade
            termination = "accepted"
        else:
            termination = "private_rejection"

    return {
        "protocol_version": MEDIATOR_PROTOCOL_VERSION,
        "action_interface_version": MEDIATOR_ACTION_INTERFACE_VERSION,
        "prompt_version": MEDIATOR_PROMPT_VERSION,
        "mechanism": "mediator",
        "episode_id": episode_id,
        "condition": condition,
        "market": dict(market),
        "mediator_prompt": mediator_prompt,
        "mediator_raw_response": mediator_raw,
        "proposal": proposal,
        "proposal_errors": errors,
        # Score the selected terms before review as a mechanism diagnostic.
        # This never overrides an LLM action or an LLM review decision.
        "proposed_outcome": score(market, proposed_trade),
        "reviews": reviews,
        "agreement": agreement,
        "termination": termination,
        "outcome": score(market, agreement),
    }


def play_consulted_mediator_episode(
    episode_id: str,
    condition: str,
    market: Mapping[str, Any],
    respond: Callable[[str, dict[str, Any]], str],
) -> dict[str, Any]:
    """Consult both agents before one mediator proposal and binding review."""
    if condition not in MEDIATOR_CONDITIONS:
        raise ValueError(f"unknown mediator condition: {condition}")
    resources = tuple(market["inventory_a"])
    consultations = []
    for actor in ("A", "B"):
        prompt = build_consultation_prompt(actor, market)
        raw = respond(prompt, consultation_schema(resources))
        consultation, errors = parse_consultation(
            raw, resources, actor, market
        )
        consultations.append({
            "actor": actor,
            "prompt": prompt,
            "raw_response": raw,
            "consultation": consultation,
            "errors": errors,
        })

    mediator_prompt = build_consulted_mediator_prompt(
        condition, market, consultations
    )
    mediator_raw = respond(mediator_prompt, proposal_schema(resources))
    parsed, proposal_errors = parse_proposal(mediator_raw, resources)
    proposal = None
    if parsed is not None:
        proposal, rule_errors = validate_proposal(parsed, market)
        proposal_errors += rule_errors

    reviews = []
    agreement = None
    proposed_trade = None
    if proposal is None:
        termination = "invalid_proposal"
    elif proposal["action"] == "no_trade":
        termination = "mediator_no_trade"
    else:
        proposed_trade = {
            "a_gives": proposal["a_gives"],
            "b_gives": proposal["b_gives"],
        }
        for actor in ("A", "B"):
            prompt = build_private_review_prompt(actor, market, proposal)
            raw = respond(prompt, review_schema())
            review, errors = parse_review(raw)
            reviews.append({
                "actor": actor,
                "prompt": prompt,
                "raw_response": raw,
                "review": review,
                "errors": errors,
            })
        if all(
            row["review"] is not None
            and row["review"]["decision"] == "accept"
            for row in reviews
        ):
            agreement = proposed_trade
            termination = "accepted"
        else:
            termination = "private_rejection"

    return {
        "protocol_version": CONSULTED_MEDIATOR_PROTOCOL_VERSION,
        "action_interface_version": CONSULTED_MEDIATOR_ACTION_INTERFACE_VERSION,
        "prompt_version": CONSULTED_MEDIATOR_PROMPT_VERSION,
        "mechanism": "consulted_mediator",
        "episode_id": episode_id,
        "condition": condition,
        "market": dict(market),
        "consultations": consultations,
        "mediator_prompt": mediator_prompt,
        "mediator_raw_response": mediator_raw,
        "proposal": proposal,
        "proposal_errors": proposal_errors,
        "proposed_outcome": score(market, proposed_trade),
        "reviews": reviews,
        "agreement": agreement,
        "termination": termination,
        "outcome": score(market, agreement),
    }


def _request_proposal(
    prompt: str,
    resources: tuple[str, ...],
    market: Mapping[str, Any],
    respond: Callable[[str, dict[str, Any]], str],
) -> tuple[str, dict[str, Any] | None, list[str]]:
    """Call, parse, and validate one mediator proposal without repairing it."""
    raw = respond(prompt, proposal_schema(resources))
    parsed, errors = parse_proposal(raw, resources)
    proposal = None
    if parsed is not None:
        proposal, rule_errors = validate_proposal(parsed, market)
        errors += rule_errors
    return raw, proposal, errors


def _trade_terms(proposal: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return executable terms only for a valid trade proposal."""
    if proposal is None or proposal.get("action") != "propose":
        return None
    return {
        "a_gives": dict(proposal["a_gives"]),
        "b_gives": dict(proposal["b_gives"]),
    }


def play_feedback_mediator_episode(
    episode_id: str,
    condition: str,
    market: Mapping[str, Any],
    respond: Callable[[str, dict[str, Any]], str],
) -> dict[str, Any]:
    """Run one initial proposal and, when needed, one feedback-based revision.

    The initial private decisions are binding if both accept. Otherwise the
    mediator sees bounded qualitative feedback and gets one final proposal.
    Final reviewers never see one another's values or feedback.
    """
    if condition not in MEDIATOR_CONDITIONS:
        raise ValueError(f"unknown mediator condition: {condition}")
    resources = tuple(market["inventory_a"])

    initial_prompt = build_mediator_prompt(condition, market)
    initial_raw, initial_proposal, initial_errors = _request_proposal(
        initial_prompt, resources, market, respond
    )

    feedback_rows: list[dict[str, Any]] = []
    revision_prompt = None
    revision_raw = None
    revised_proposal = None
    revision_errors: list[str] = []
    final_reviews: list[dict[str, Any]] = []
    agreement = None
    selected_proposal = None

    if initial_proposal is None:
        termination = "invalid_initial_proposal"
    elif initial_proposal["action"] == "no_trade":
        termination = "initial_no_trade"
        selected_proposal = initial_proposal
    else:
        initial_trade = _trade_terms(initial_proposal)
        for actor in ("A", "B"):
            prompt = build_feedback_review_prompt(actor, market, initial_proposal)
            raw = respond(prompt, feedback_schema())
            feedback, errors = parse_feedback(raw)
            feedback_rows.append({
                "actor": actor,
                "prompt": prompt,
                "raw_response": raw,
                "feedback": feedback,
                "errors": errors,
            })

        if all(
            row["feedback"] is not None
            and row["feedback"]["decision"] == "accept"
            for row in feedback_rows
        ):
            selected_proposal = initial_proposal
            agreement = initial_trade
            termination = "accepted_initial"
        else:
            revision_prompt = build_revision_prompt(
                condition, market, initial_proposal, feedback_rows
            )
            revision_raw, revised_proposal, revision_errors = _request_proposal(
                revision_prompt, resources, market, respond
            )
            if revised_proposal is None:
                termination = "invalid_revision"
            elif revised_proposal["action"] == "no_trade":
                selected_proposal = revised_proposal
                termination = "mediator_revision_no_trade"
            else:
                selected_proposal = revised_proposal
                revised_trade = _trade_terms(revised_proposal)
                for actor in ("A", "B"):
                    prompt = build_final_review_prompt(
                        actor, market, revised_proposal
                    )
                    raw = respond(prompt, review_schema())
                    review, errors = parse_review(raw)
                    final_reviews.append({
                        "actor": actor,
                        "prompt": prompt,
                        "raw_response": raw,
                        "review": review,
                        "errors": errors,
                    })
                if all(
                    row["review"] is not None
                    and row["review"]["decision"] == "accept"
                    for row in final_reviews
                ):
                    agreement = revised_trade
                    termination = "accepted_revised"
                else:
                    termination = "final_private_rejection"

    selected_trade = _trade_terms(selected_proposal)
    return {
        "protocol_version": FEEDBACK_MEDIATOR_PROTOCOL_VERSION,
        "action_interface_version": FEEDBACK_MEDIATOR_ACTION_INTERFACE_VERSION,
        "prompt_version": FEEDBACK_MEDIATOR_PROMPT_VERSION,
        "mechanism": "feedback_mediator",
        "episode_id": episode_id,
        "condition": condition,
        "market": dict(market),
        "mediator_prompt": initial_prompt,
        "mediator_raw_response": initial_raw,
        "initial_proposal": initial_proposal,
        "initial_proposal_errors": initial_errors,
        "initial_proposed_outcome": score(market, _trade_terms(initial_proposal)),
        "feedback": feedback_rows,
        "revision_prompt": revision_prompt,
        "revision_raw_response": revision_raw,
        "revised_proposal": revised_proposal,
        "revision_errors": revision_errors,
        # These common fields let the existing mediator analysis summarize the
        # terms that actually reached the binding decision stage.
        "proposal": selected_proposal,
        "proposal_errors": (
            revision_errors if revision_prompt is not None else initial_errors
        ),
        "proposed_outcome": score(market, selected_trade),
        "reviews": final_reviews,
        "agreement": agreement,
        "termination": termination,
        "outcome": score(market, agreement),
    }
