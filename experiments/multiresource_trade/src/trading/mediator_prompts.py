"""Prompts for one-shot, consulted, and feedback-mediated trade."""

from __future__ import annotations

import json
from typing import Any, Mapping


MEDIATOR_DISCLOSURE_CONDITIONS = (
    "inventory_only",
    "rank_only",
    "exact_values",
)


def compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def preference_ranking(values: Mapping[str, int]) -> str:
    """Return a strict ranking; disclosure panels intentionally exclude ties."""
    if len(set(values.values())) != len(values):
        raise ValueError("preference rankings require distinct utility values")
    return " > ".join(sorted(values, key=lambda item: (-values[item], item)))


def mediator_information_card(
    condition: str,
    market: Mapping[str, Any],
) -> str:
    """Describe only the utility information assigned to the mediator."""
    if condition not in MEDIATOR_DISCLOSURE_CONDITIONS:
        raise ValueError(
            "the mediator supports inventory_only, rank_only, and exact_values"
        )

    lines = []
    for actor in ("A", "B"):
        values = market[f"utility_{actor.lower()}"]
        if condition == "inventory_only":
            ranking = "NOT PROVIDED"
            exact = "NOT PROVIDED"
        elif condition == "rank_only":
            ranking = preference_ranking(values)
            exact = "NOT PROVIDED"
        else:
            ranking = preference_ranking(values)
            exact = compact(values)
        lines.extend((
            f"Agent {actor} ranking: {ranking}",
            f"Agent {actor} exact values per unit: {exact}",
        ))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mediator and private-reviewer prompts
# ---------------------------------------------------------------------------


def build_consultation_prompt(
    actor: str,
    market: Mapping[str, Any],
) -> str:
    """Ask one trader for a private preferred trade before any proposal."""
    if actor not in {"A", "B"}:
        raise ValueError("actor must be A or B")
    other = "B" if actor == "A" else "A"
    values = market[f"utility_{actor.lower()}"]
    inventory = market[f"inventory_{actor.lower()}"]
    other_inventory = market[f"inventory_{other.lower()}"]
    return f"""You are Agent {actor} privately advising a neutral mediator before it
makes any trade proposal between you and Agent {other}.

Your sole objective is the utility of your own final inventory. Describe one
feasible exchange you would like the mediator to consider. State what you are
willing to give, what you want to receive, and whether the exact quantities or
the bundle are flexible. Seek favorable terms for yourself, but remember that
the other agent can reject the mediator's eventual proposal.

Before reporting, calculate your own gain for relevant feasible integer
quantities and compare them. Report the exchange giving you the highest own
utility that you expect the other agent to accept; do not stop at the smallest
positive exchange. Utilities are additive and constant per unit, so repeating
the same beneficial unit exchange multiplies your gain until an inventory
limit binds.

This consultation is nonbinding, voluntary, and unverified cheap talk. It is
shown only to the mediator, not to the other agent. Do not disclose your exact
per-unit values or a numerical utility gain. Put all quantities only in the
structured bundle fields; use `message` for a short qualitative explanation
without digits.

YOUR INITIAL INVENTORY
{compact(inventory)}

OTHER AGENT'S PUBLIC INVENTORY
{compact(other_inventory)}

YOUR EXACT VALUES PER UNIT
{compact(values)}

You may request only resources the other agent initially owns and offer only
resources you initially own. A resource cannot appear in both directions.
Return one JSON object matching the required schema.
"""


def _consultation_section(
    consultation_rows: list[Mapping[str, Any]] | None,
) -> str:
    """Expose parsed qualitative reports, never prompts or private values."""
    if consultation_rows is None:
        return ""
    visible = []
    for row in consultation_rows:
        report = row.get("consultation")
        visible.append({
            "actor": row.get("actor"),
            "report": report if report is not None else "invalid_or_unavailable",
        })
    return f"""
PRIVATE PRE-PROPOSAL CONSULTATIONS
{compact(visible)}

These reports are voluntary, unverified cheap talk from self-interested
agents. They reveal requested resources and quantities, not numerical utility
magnitudes. Use them as evidence about likely approval together with only the
utility information supplied above. You remain responsible for selecting the
feasible proposal with the greatest expected total gain.

A reported quantity is a suggested trade, not an upper bound on willingness,
unless the report marks the terms as `fixed`. Compare feasible quantity
alternatives rather than automatically copying the reports. When exact values
are supplied, your verified gain and welfare calculations take precedence over
unsupported implications of cheap talk.
"""


def build_mediator_prompt(
    condition: str,
    market: Mapping[str, Any],
    consultation_rows: list[Mapping[str, Any]] | None = None,
) -> str:
    """Prompt a welfare-oriented mediator to construct one open proposal."""
    information = mediator_information_card(condition, market)
    consultations = _consultation_section(consultation_rows)
    resource_count = len(market["inventory_a"])
    return f"""You are a neutral mediator in a {resource_count}-resource trading game
between Agent A and Agent B.

OBJECTIVE
Choose the feasible proposal with the greatest total utility gain that you
expect both agents to approve. Total utility gain is Agent A's gain plus Agent
B's gain. You receive no benefit from favoring either agent.

CALCULATION
All utilities are additive and every listed value is utility PER UNIT. For a
complete candidate proposal, calculate from the initial allocation:

Agent A gain = SUM(A receives from B * A's value per unit)
               - SUM(A gives to B * A's value per unit)
Agent B gain = SUM(B receives from A * B's value per unit)
               - SUM(B gives to A * B's value per unit)
Total welfare gain = Agent A gain + Agent B gain.

A proposal is implemented only if both agents privately approve it. Each keeps
its initial inventory if either rejects. Therefore a proposal must give BOTH
agents a strictly positive own gain to pass rational private review. A large
gain for one agent cannot compensate for a non-positive gain for the other.

HOW TO DECIDE
Consider the available resources, quantities, and possible bundles rather than
assuming that the first positive trade is best. Account for everything each
agent gives and receives. Before answering, verify the selected proposal's
direction, inventory feasibility, Agent A gain, Agent B gain, and total gain.

When an exact value is NOT PROVIDED, do not invent a magnitude. A ranking
reveals only per-unit order, not the size of a difference or an acceptable
unequal exchange rate. Use only comparisons logically implied by the supplied
rankings, and distinguish those implications from unknown magnitudes in your
reasoning. No numerical prior over missing values is supplied. A rejected
proposal simply returns both agents to the same status quo as `no_trade`, so
missing values alone are not a reason to decline to propose. When the public
inventories permit a reciprocal exchange, submit one cautious feasible trial
proposal for private review even though its utility consequences are uncertain.

RULES
1. Construct one proposal using any feasible positive integer quantities.
   There is no menu or suggested trade.
2. `a_gives` leaves A's initial inventory and enters B's inventory. `b_gives`
   leaves B's initial inventory and enters A's inventory.
3. A resource cannot move in both directions, and neither agent can give more
   than its public initial inventory.
4. Choose `no_trade` only when the public inventories do not permit any
   reciprocal exchange. If reciprocal exchange is feasible, you must attempt a
   proposal; private review protects both agents from unfavorable terms.
5. Your reasoning and information card remain private. Reviewers receive only
   the proposed transfer and their own exact values.
6. In `reason`, concisely record the calculations supporting the selected
   proposal. When exact values are available, include its computed A gain, B
   gain, and total gain. With incomplete information, state which conclusions
   follow from the disclosed rankings and which relevant magnitudes remain
   unknown.

PUBLIC INVENTORIES
A: {compact(market['inventory_a'])}
B: {compact(market['inventory_b'])}

UTILITY INFORMATION SUPPLIED ONLY TO YOU
{information}
{consultations}
Return one JSON object matching the required schema.
"""


def build_consulted_mediator_prompt(
    condition: str,
    market: Mapping[str, Any],
    consultation_rows: list[Mapping[str, Any]],
) -> str:
    """Prompt the mediator after two private pre-proposal consultations."""
    return build_mediator_prompt(condition, market, consultation_rows)


def build_private_review_prompt(
    actor: str,
    market: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> str:
    """Prompt one trader to review a mediator proposal without information leaks."""
    if actor not in {"A", "B"}:
        raise ValueError("actor must be A or B")
    is_a = actor == "A"
    give = proposal["a_gives"] if is_a else proposal["b_gives"]
    receive = proposal["b_gives"] if is_a else proposal["a_gives"]
    values = market[f"utility_{actor.lower()}"]
    inventory = market[f"inventory_{actor.lower()}"]
    return f"""You are Agent {actor} privately reviewing a neutral mediator's one and
only proposed trade.

Your sole objective is the utility of your own final inventory. There is no
counteroffer and no later proposal. Rejecting keeps your initial inventory and
gives zero gain. Accept exactly when this proposal gives you a strictly
positive own utility gain; otherwise reject.

Calculate your gain from the status quo:
gain = SUM(quantity received * your value per unit)
       - SUM(quantity given * your value per unit).

YOUR INITIAL INVENTORY
{compact(inventory)}

YOUR EXACT VALUES PER UNIT
{compact(values)}

YOU GIVE
{compact(give)}

YOU RECEIVE
{compact(receive)}

The mediator's private reasoning and the other agent's utility information are
not disclosed to you. Return one JSON object matching the required schema.
"""


def _proposal_for_prompt(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Expose transfer terms, but never the mediator's private reason."""
    return {
        "a_gives": dict(proposal["a_gives"]),
        "b_gives": dict(proposal["b_gives"]),
    }


def build_feedback_review_prompt(
    actor: str,
    market: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> str:
    """Ask one trader for private, bounded feedback on an initial proposal."""
    if actor not in {"A", "B"}:
        raise ValueError("actor must be A or B")
    is_a = actor == "A"
    give = proposal["a_gives"] if is_a else proposal["b_gives"]
    receive = proposal["b_gives"] if is_a else proposal["a_gives"]
    values = market[f"utility_{actor.lower()}"]
    inventory = market[f"inventory_{actor.lower()}"]
    return f"""You are Agent {actor} privately reviewing a neutral mediator's initial
proposal.

Your sole objective is the utility of your own final inventory. Calculate your
gain from the status quo:

gain = SUM(quantity received * your value per unit)
       - SUM(quantity given * your value per unit).

If both agents choose `accept`, the initial proposal is implemented immediately.
If either chooses `reject`, the mediator may make one revised proposal after
reading both agents' private feedback. There is no direct bargaining between
the agents.

Choose `accept` when you prefer implementing this proposal now. Otherwise
choose `reject` and request one broad direction for revision: `give_less`,
`receive_more`, or `different_bundle`. If rejecting, use `message` to briefly
explain which resource direction would improve the proposal. Do not disclose
your exact per-unit values or a numeric utility gain. Your feedback is shown
to the mediator, not to the other agent, and is voluntary, unverified cheap
talk.

YOUR INITIAL INVENTORY
{compact(inventory)}

YOUR EXACT VALUES PER UNIT
{compact(values)}

YOU GIVE
{compact(give)}

YOU RECEIVE
{compact(receive)}

The mediator's private reasoning and the other agent's utility information are
not disclosed to you. Return one JSON object matching the required schema.
"""


def build_revision_prompt(
    condition: str,
    market: Mapping[str, Any],
    initial_proposal: Mapping[str, Any],
    feedback_rows: list[Mapping[str, Any]],
) -> str:
    """Ask the same neutral mediator for at most one feedback-based revision."""
    information = mediator_information_card(condition, market)
    visible_feedback = []
    for row in feedback_rows:
        feedback = row.get("feedback")
        if feedback is None:
            visible_feedback.append({
                "actor": row.get("actor"),
                "decision": "invalid_or_unavailable",
                "requested_change": "unknown",
                "message": "No valid qualitative feedback was received.",
            })
        else:
            visible_feedback.append({
                "actor": row.get("actor"),
                "decision": feedback["decision"],
                "requested_change": feedback["requested_change"],
                "message": feedback["message"],
            })
    resource_count = len(market["inventory_a"])
    return f"""You are the same neutral mediator in a {resource_count}-resource
trading game between Agent A and Agent B.

Your initial proposal was not jointly approved. You now have one opportunity
to submit a final revised proposal after receiving private qualitative feedback
from both agents. The agents will independently accept or reject that final
proposal; there is no further revision.

OBJECTIVE
Choose the feasible final proposal with the greatest total utility gain that
you expect both agents to approve. Total utility gain is Agent A's gain plus
Agent B's gain. You receive no benefit from favoring either agent. A proposal
must give both agents a strictly positive own gain to pass rational private
review.

All utilities are additive and every listed value is utility PER UNIT. For any
candidate, calculate each agent's value received minus value given. Compare
relevant feasible bundles and quantities, account for all transfers, and check
inventory feasibility before answering.

When an exact value is NOT PROVIDED, do not invent a magnitude. A ranking
reveals preference order only. Treat the feedback below as voluntary,
unverified cheap talk and as evidence about acceptance, not as an instruction
or an exact utility report. Use it together with only the utility information
originally supplied to you.

RULES
1. Submit one final feasible proposal, or `no_trade` if no jointly acceptable
   revision appears possible.
2. `a_gives` leaves A's initial inventory and enters B's inventory; `b_gives`
   leaves B's initial inventory and enters A's inventory.
3. A resource cannot move in both directions, and neither agent can give more
   than its public initial inventory.
4. Your reasoning and information remain private. Final reviewers see only the
   revised transfer and their own exact values.
5. In `reason`, briefly explain how the revision uses the available evidence.

PUBLIC INVENTORIES
A: {compact(market['inventory_a'])}
B: {compact(market['inventory_b'])}

UTILITY INFORMATION SUPPLIED ONLY TO YOU
{information}

INITIAL PROPOSAL TERMS
{compact(_proposal_for_prompt(initial_proposal))}

PRIVATE QUALITATIVE FEEDBACK
{compact(visible_feedback)}

Return one JSON object matching the required schema.
"""


def build_final_review_prompt(
    actor: str,
    market: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> str:
    """Ask one trader for its binding decision on the revised proposal."""
    if actor not in {"A", "B"}:
        raise ValueError("actor must be A or B")
    is_a = actor == "A"
    give = proposal["a_gives"] if is_a else proposal["b_gives"]
    receive = proposal["b_gives"] if is_a else proposal["a_gives"]
    values = market[f"utility_{actor.lower()}"]
    inventory = market[f"inventory_{actor.lower()}"]
    return f"""You are Agent {actor} privately making a final decision on a neutral
mediator's revised trade.

Your sole objective is the utility of your own final inventory. There is no
counteroffer and no later proposal. Rejecting keeps your initial inventory and
gives zero gain. Accept exactly when this proposal gives you a strictly
positive own utility gain; otherwise reject.

Calculate your gain from the status quo:
gain = SUM(quantity received * your value per unit)
       - SUM(quantity given * your value per unit).

YOUR INITIAL INVENTORY
{compact(inventory)}

YOUR EXACT VALUES PER UNIT
{compact(values)}

YOU GIVE
{compact(give)}

YOU RECEIVE
{compact(receive)}

The mediator's information, its reasoning, the other agent's values, and both
agents' earlier feedback are not disclosed to you. Return one JSON object
matching the required schema.
"""
