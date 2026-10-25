"""The prompt and disclosure cards for peer negotiation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .. import (
    ACTION_INTERFACE_VERSION,
    ACTION_INTERFACE_VERSIONS,
    LEGACY_ACTION_INTERFACE_VERSION,
)

CONDITIONS = (
    "inventory_only",
    "rank_only",
    "exact_values",
    "first_exact",
    "second_exact",
)

ACTIVE_AGENT_POLICY = "plain_dialogue_self_interest"
ACTIVE_PROMPT_VERSION = "plain_dialogue_detailed_information_20260822"
AGENT_POLICIES = (ACTIVE_AGENT_POLICY,)


def prompt_version(agent_policy: str) -> str:
    """Return the supported prompt's recorded version."""
    if agent_policy == ACTIVE_AGENT_POLICY:
        return ACTIVE_PROMPT_VERSION
    raise ValueError(f"unsupported agent policy: {agent_policy}")


def objective_text(agent_policy: str = ACTIVE_AGENT_POLICY) -> str:
    """Return the active self-interest and bounded-discovery objective."""
    if agent_policy != ACTIVE_AGENT_POLICY:
        raise ValueError(f"unsupported agent policy: {agent_policy}")
    return """Your sole objective is to obtain the greatest possible utility for your own
final inventory.

Apply this decision process before every action:
1. Calculate each candidate from the status quo:

   own gain = SUM(quantity received * your value per unit)
              - SUM(quantity given * your value per unit).

   Final utility equals initial utility plus this gain. Each giver must
   initially own every quantity offered; units received in the same trade
   cannot be given onward in that trade.
2. Compare feasible positive-integer bundles systematically, including small
   quantities and quantities near inventory limits; do not stop at the first
   mutually beneficial offer. With additive, constant per-unit values,
   repeating a unit bundle k times multiplies its gain by k until inventory
   binds.
3. Use only counterpart information actually provided. A ranking can determine
   the sign of equal-quantity comparisons but not magnitudes or acceptable
   unequal exchange rates; infer only comparisons justified by its order.
   With exact values, calculate counterpart gain for each serious candidate:
   value it receives minus value it gives. Never invent missing values or
   assign numbers to a ranking.
4. When an unknown preference or magnitude could change your action and
   messages remain, use targeted questions, explanations, proposals, and
   counteroffers. Update your beliefs from replies, proposals, rejections, and
   counteroffers. Statements are voluntary and unverified; balance learning
   against disagreement risk. With no active offer, `wait` may carry a targeted
   question. Use at most one of your turns solely for discovery before your
   first proposal. Do not repeat an unanswered question: use the public
   inventories, your own values, any available ranking, and the dialogue to
   make the best feasible offer you can under uncertainty. You do not need
   exact counterpart values to propose. If asked a question, answer as much as
   is strategically appropriate or advance bargaining with an offer; do not
   reply only by repeating the question.
5. When proposing, seek the feasible agreement giving you the highest own
   utility among those you realistically expect the counterpart to accept.
   Counterpart utility matters only for predicting acceptance; it is not part
   of your objective. A positive counterpart gain may not ensure acceptance.
6. Do not accept merely because an offer gives positive own gain. Prefer a
   better likely acceptable counteroffer when one remains, while accounting
   for the remaining messages and disagreement risk. Equal quantities, full
   exchange, equal gains, and immediate agreement are not objectives. Verify
   inventory feasibility and transfer direction before proposing."""


def compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def preference_ranking(values: Mapping[str, int]) -> str:
    """Return a strict preference order for the ranking treatment."""
    if len(set(values.values())) != len(values):
        raise ValueError("preference rankings require distinct utility values")
    return " > ".join(sorted(values, key=lambda item: (-values[item], item)))


def opponent_information(condition: str, actor: str, first_actor: str) -> str:
    """Return counterpart information from the condition, not market metadata.

    Main-study conditions disclose the same level to both actors. Only
    first_exact and second_exact make disclosure depend on the actor's role.
    """
    if condition == "inventory_only":
        return "inventory"
    if condition == "rank_only":
        return "ranking"
    if condition == "exact_values":
        return "exact"
    is_first = actor == first_actor
    if condition == "first_exact":
        return "exact" if is_first else "ranking"
    if condition == "second_exact":
        return "ranking" if is_first else "exact"
    raise ValueError(f"unknown condition: {condition}")


def history_text(
    transcript: list[dict[str, Any]],
    compact_output: bool = False,
    action_interface: str = ACTION_INTERFACE_VERSION,
) -> str:
    """Render the complete public history without hidden reasoning."""
    events = []
    for turn in transcript:
        event = {"turn": turn["turn"], "actor": turn["actor"]}
        if turn["action"] is None:
            event["invalid_response"] = turn["raw_response"]
            event["errors"] = turn["errors"]
        else:
            action = turn["action"]
            event["action"] = {
                "action": action["action"],
                "message": action["message"],
            }
            if action_interface == LEGACY_ACTION_INTERFACE_VERSION:
                event["action"]["proposal_id"] = action["proposal_id"]
            if action["action"] == "propose":
                event["action"].update({
                    "proposer": action["proposer"],
                    "A_gives_to_B": action["a_gives"],
                    "B_gives_to_A": action["b_gives"],
                })
        events.append(event)
    indent = None if compact_output else 2
    separators = (",", ":") if compact_output else None
    return json.dumps(events, indent=indent, separators=separators, sort_keys=True)


@dataclass(frozen=True)
class _PromptState:
    actor: str
    other: str
    market: Mapping[str, Any]
    transcript: list[dict[str, Any]]
    own_values: Mapping[str, int]
    ranking_card: str
    value_card: str
    first_actor: str
    turn: int
    total_messages: int
    phase: str
    legal: list[str]
    proposals_made: int
    proposal_limit: int
    discovery_rule: str
    active_offer_text: str
    action_interface: str
    uses_legacy_ids: bool


def _prepare_state(
    condition: str,
    actor: str,
    market: Mapping[str, Any],
    transcript: list[dict[str, Any]],
    active_offer: Mapping[str, Any] | None,
    turn: int,
    max_messages: int,
    proposals_made: int,
    proposal_limit: int,
    first_actor: str,
    discovery_messages: int,
    action_interface: str,
) -> _PromptState:
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")
    if first_actor not in {"A", "B"}:
        raise ValueError("first_actor must be A or B")
    if action_interface not in ACTION_INTERFACE_VERSIONS:
        raise ValueError(f"unknown action interface: {action_interface}")

    other = "B" if actor == "A" else "A"
    own_values = market[f"utility_{actor.lower()}"]
    other_values = market[f"utility_{other.lower()}"]
    information = opponent_information(condition, actor, first_actor)
    ranking_card = (
        preference_ranking(other_values)
        if information in {"ranking", "exact"}
        else "NOT PROVIDED"
    )
    value_card = compact(other_values) if information == "exact" else "NOT PROVIDED"

    in_discovery = turn <= discovery_messages
    bargaining_turn = turn - discovery_messages
    if in_discovery:
        legal = ["wait"]
    else:
        may_answer = active_offer is not None and active_offer["proposer"] != actor
        legal = ["accept"] if may_answer else []
        if bargaining_turn < max_messages and proposals_made < proposal_limit:
            legal.append("propose")
        legal.append("wait")

    discovery_rule = ""
    if discovery_messages:
        discovery_rule = f"""
8. Before bargaining there are {discovery_messages} discovery messages, one
per agent in alternating order. During discovery, communicate using `wait`;
offers and acceptance are not legal. You may ask for or disclose preference
information, but statements remain voluntary and unverified. These messages do
not reduce the {max_messages}-message bargaining horizon."""
    phase = (
        f"Discovery message {turn} of {discovery_messages}"
        if in_discovery
        else f"Bargaining message {bargaining_turn} of {max_messages}"
    )

    if active_offer:
        displayed_offer = {
            "proposer": active_offer["proposer"],
            "A_gives_to_B": active_offer["a_gives"],
            "B_gives_to_A": active_offer["b_gives"],
            "message": active_offer["message"],
        }
        if action_interface == LEGACY_ACTION_INTERFACE_VERSION:
            displayed_offer = {
                "proposal_id": active_offer["proposal_id"],
                **displayed_offer,
            }
        active_offer_text = compact(displayed_offer)
    else:
        active_offer_text = "NONE"

    return _PromptState(
        actor=actor,
        other=other,
        market=market,
        transcript=transcript,
        own_values=own_values,
        ranking_card=ranking_card,
        value_card=value_card,
        first_actor=first_actor,
        turn=turn,
        total_messages=discovery_messages + max_messages,
        phase=phase,
        legal=legal,
        proposals_made=proposals_made,
        proposal_limit=proposal_limit,
        discovery_rule=discovery_rule,
        active_offer_text=active_offer_text,
        action_interface=action_interface,
        uses_legacy_ids=action_interface == LEGACY_ACTION_INTERFACE_VERSION,
    )


def _render_prompt(state: _PromptState) -> str:
    """Render the complete active prompt with plain headings and JSON output."""
    proposal_rule = (
        "For `propose`, leave `proposal_id` empty. For `accept`, copy the "
        "active proposal ID and leave both transfers empty. `wait` makes no "
        "offer or acceptance and declines any active offer."
        if state.uses_legacy_ids
        else "For `accept`, leave both transfers empty; it accepts the active "
        "offer. `wait` makes no offer or acceptance and declines any active offer."
    )
    acceptance_rule = (
        "To accept, copy the active proposal ID without changing its transfers."
        if state.uses_legacy_ids
        else "`accept` applies to the currently active offer; do not repeat its transfers."
    )
    return f"""You are Agent {state.actor} in a strategic {len(state.market['inventory_a'])}-resource trading game with Agent {state.other}.

OBJECTIVE
{objective_text()}

GAME RULES
- Inventories are initial unit counts. Every listed value is additive utility
  PER UNIT. Final utility is the sum of final quantities multiplied by your
  per-unit values.
- If no trade is accepted, both agents keep their initial inventories and
  receive zero gain from trade.
- Agents alternate messages. You may propose any feasible positive-integer
  trade.
- A counteroffer replaces the active offer. Only the recipient of the active
  offer may accept it.
- You may make at most {state.proposal_limit} proposals; {state.proposal_limit - state.proposals_made} remain. At most
  one trade occurs, and acceptance ends the game.
- Conversation may reveal preferences, but statements are voluntary,
  unverified cheap talk.
- `you_give` leaves your inventory and `you_receive` enters it.
  {proposal_rule} A final-message proposal is illegal.{state.discovery_rule}

PUBLIC INVENTORIES
Agent A: {compact(state.market['inventory_a'])}
Agent B: {compact(state.market['inventory_b'])}

PRIVATE INFORMATION
Your exact values per unit: {compact(state.own_values)}
Other agent's ranking: {state.ranking_card}
Other agent's exact values per unit: {state.value_card}

HOW TO READ THE INFORMATION
- An inventory number is the quantity of that resource initially owned.
- A value is utility PER UNIT, not the total value of the inventory. Your final
  utility is the sum, over resources, of final quantity times per-unit value.
- In a ranking, the resource on the left of > has greater per-unit value than
  the resource on the right. A ranking reveals only this order; it does not
  reveal the size of the difference or either exact value.
- A ranking is usable without exact magnitudes for comparisons whose sign is
  implied by preference order, but it does not reveal acceptable unequal
  exchange rates.
- An exact-value card gives the other agent's utility per unit under the same
  additive rule.
- NOT PROVIDED means that information is unknown to you. Do not assume a
  particular missing value.

DECISION REMINDER
Choose the legal action you expect to produce the greatest utility for your
own final inventory. Counterpart utility is evidence about acceptance, not
part of your objective.

STATE
First mover: Agent {state.first_actor}
{state.phase}
Overall message {state.turn} of {state.total_messages}
Legal actions: {', '.join(state.legal)}
Active offer: {state.active_offer_text}

PUBLIC HISTORY
{history_text(state.transcript, compact_output=True, action_interface=state.action_interface)}

ACTION GUIDE
- `you_give` always leaves your inventory; `you_receive` enters it. Public
  history describes the same directions as `A_gives_to_B` and `B_gives_to_A`.
- `wait` may carry a conversational message and declines any active offer.
- {acceptance_rule}

OUTPUT
Return one JSON object matching the required schema. Do not add text outside
the JSON object.
"""


def build_prompt(
    condition: str,
    actor: str,
    market: Mapping[str, Any],
    transcript: list[dict[str, Any]],
    active_offer: Mapping[str, Any] | None,
    turn: int,
    max_messages: int,
    proposals_made: int,
    proposal_limit: int,
    agent_policy: str = ACTIVE_AGENT_POLICY,
    first_actor: str = "A",
    discovery_messages: int = 0,
    action_interface: str = ACTION_INTERFACE_VERSION,
) -> str:
    """Build the prompt."""
    if agent_policy != ACTIVE_AGENT_POLICY:
        raise ValueError(f"unsupported agent policy: {agent_policy}")
    state = _prepare_state(
        condition,
        actor,
        market,
        transcript,
        active_offer,
        turn,
        max_messages,
        proposals_made,
        proposal_limit,
        first_actor,
        discovery_messages,
        action_interface,
    )
    return _render_prompt(state)
