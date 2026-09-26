"""Offline measures of search, communication, and bargaining distortion.

All action-based measures are calculated exactly from saved proposals and the
market utility functions.  Message measures use transparent regular
expressions; they are useful descriptive diagnostics, not semantic labels from
another language model.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .behavior import CONDITION_ORDER, episode_offers, score_offer, valid_turns
from .metrics import mean


HIGH_WELFARE = 0.90
EPSILON = 1e-9


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def summarize_search_and_bargaining(
    rows: list[dict[str, Any]], high_welfare: float = HIGH_WELFARE
) -> dict[str, float | int]:
    """Separate proposal search from conversion of proposals into outcomes."""
    best_ir = []
    best_by_role: dict[str, list[float]] = defaultdict(list)
    first_times: dict[str, list[int]] = defaultdict(list)
    reached = defaultdict(int)
    all_offers: list[dict[str, Any]] = []

    for episode in rows:
        offers = episode_offers(episode)
        all_offers.extend(offers)
        ir = [offer for offer in offers if offer["ir"]]
        best_ir.append(max((offer["welfare"] for offer in ir), default=0.0))
        for role in ("proposer", "responder"):
            role_ir = [offer for offer in ir if offer["role"] == role]
            best_by_role[role].append(
                max((offer["welfare"] for offer in role_ir), default=0.0)
            )
        for label, predicate in (
            ("ir", lambda offer: offer["ir"]),
            ("high", lambda offer: offer["ir"] and offer["welfare"] >= high_welfare),
            ("first_best", lambda offer: offer["ir"] and abs(offer["welfare"] - 1) < EPSILON),
        ):
            times = [offer["message_number"] for offer in offers if predicate(offer)]
            if times:
                reached[label] += 1
                first_times[label].append(min(times))

    final_welfare = mean([
        episode["outcome"]["normalized_welfare"] for episode in rows
    ])
    high = [
        offer for offer in all_offers
        if offer["ir"] and offer["welfare"] >= high_welfare
    ]
    first_best = [
        offer for offer in all_offers
        if offer["ir"] and abs(offer["welfare"] - 1) < EPSILON
    ]
    ir_offers = [offer for offer in all_offers if offer["ir"]]
    result: dict[str, float | int] = {
        "episodes": len(rows),
        "offers": len(all_offers),
        "ir_offers": len(ir_offers),
        "mean_best_ir_offer_welfare": mean(best_ir),
        "mean_final_welfare": final_welfare,
        "mean_realization_loss": mean(best_ir) - final_welfare,
        "proposer_mean_best_ir_offer_welfare": mean(best_by_role["proposer"]),
        "responder_mean_best_ir_offer_welfare": mean(best_by_role["responder"]),
        "high_welfare_offers": len(high),
        "high_welfare_rejected": sum(not offer["accepted"] for offer in high),
        "high_welfare_rejection_rate": mean([
            float(not offer["accepted"]) for offer in high
        ]),
        "first_best_offers": len(first_best),
        "first_best_rejected": sum(not offer["accepted"] for offer in first_best),
        "mean_ir_counterpart_gain": mean([
            offer["other_gain"] for offer in ir_offers
        ]),
        "mean_ir_counterpart_surplus_share": mean([
            offer["other_gain"] / offer["welfare"]
            for offer in ir_offers if offer["welfare"] > 0
        ]),
    }
    for label in ("ir", "high", "first_best"):
        result[f"episodes_reaching_{label}_offer_rate"] = _rate(
            reached[label], len(rows)
        )
        result[f"mean_messages_to_first_{label}_offer"] = mean(first_times[label])
    return result


def summarize_counteroffers(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    """Measure how a counteroffer changes the rejected offer's payoff vector.

    A counteroffer is *extractive* when it raises the countering agent's payoff,
    lowers the counterpart's payoff, and lowers total welfare.  This implements
    the preregisterable definition in the report without interpreting prose.
    """
    comparisons = []
    for episode in rows:
        turns = valid_turns(episode)
        for prior_turn, counter_turn in zip(turns, turns[1:]):
            if prior_turn["action"].get("action") != "propose":
                continue
            if counter_turn["action"].get("action") != "propose":
                continue
            if prior_turn["actor"] == counter_turn["actor"]:
                continue
            prior = score_offer(episode, prior_turn)
            counter = score_offer(episode, counter_turn)
            # Re-express the prior proposal from the countering actor's view.
            prior_self = prior["other_gain"]
            prior_other = prior["own_gain"]
            delta_self = counter["own_gain"] - prior_self
            delta_other = counter["other_gain"] - prior_other
            delta_welfare = counter["welfare"] - prior["welfare"]
            comparisons.append({
                "delta_self": delta_self,
                "delta_other": delta_other,
                "delta_welfare": delta_welfare,
                "surplus_demand": delta_self > EPSILON and delta_other < -EPSILON,
                "extractive": (
                    delta_self > EPSILON
                    and delta_other < -EPSILON
                    and delta_welfare < -EPSILON
                ),
                "rejected_high_welfare": (
                    prior["ir"] and prior["welfare"] >= HIGH_WELFARE
                ),
                "extractive_after_high_welfare": (
                    prior["ir"]
                    and prior["welfare"] >= HIGH_WELFARE
                    and delta_self > EPSILON
                    and delta_other < -EPSILON
                    and delta_welfare < -EPSILON
                ),
            })
    return {
        "counteroffers": len(comparisons),
        "mean_self_gain_change": mean([row["delta_self"] for row in comparisons]),
        "mean_other_gain_change": mean([row["delta_other"] for row in comparisons]),
        "mean_welfare_change": mean([row["delta_welfare"] for row in comparisons]),
        "surplus_demand_counteroffers": sum(row["surplus_demand"] for row in comparisons),
        "surplus_demand_counteroffer_rate": mean([
            float(row["surplus_demand"]) for row in comparisons
        ]),
        "extractive_counteroffers": sum(row["extractive"] for row in comparisons),
        "extractive_counteroffers_per_episode": _rate(len([
            row for row in comparisons if row["extractive"]
        ]), len(rows)),
        "extractive_counteroffer_rate": mean([
            float(row["extractive"]) for row in comparisons
        ]),
        "high_welfare_offers_followed_by_counteroffer": sum(
            row["rejected_high_welfare"] for row in comparisons
        ),
        "extractive_after_high_welfare": sum(
            row["extractive_after_high_welfare"] for row in comparisons
        ),
    }


_DISCOVERY_QUESTION = re.compile(
    r"\b(?:what|which|how many|how much|could you|can you|would you share)\b"
    r"[^?]{0,180}\b(?:value|valuation|prefer|preference|ranking|resource|"
    r"quantity|amount|ratio|rate|want|need|give|receive)\b",
    re.IGNORECASE,
)
_RATE_LANGUAGE = re.compile(
    r"\b(?:ratio|rate|exchange rate|minimum|maximum|at least|at most|"
    r"would accept|can accept|willing to)\b|\b\d+\s*:\s*\d+\b",
    re.IGNORECASE,
)


def _message(turn: dict[str, Any]) -> str:
    return str((turn.get("action") or {}).get("message") or "")


def _is_discovery_question(message: str) -> bool:
    """Exclude generic 'deal?' language and retain targeted information asks."""
    return "?" in message and bool(_DISCOVERY_QUESTION.search(message))


def _preference_claims(
    message: str, actor: str, market: dict[str, Any]
) -> list[bool]:
    """Return truth labels for parseable comparative or numeric value claims."""
    text = message.lower()
    resources = [str(item).lower() for item in market["resources"]]
    values = {
        str(item).lower(): value
        for item, value in market[f"utility_{actor.lower()}"].items()
    }
    claims: list[bool] = []
    seen: set[tuple[str, ...]] = set()

    for better in resources:
        for worse in resources:
            if better == worse:
                continue
            patterns = (
                rf"\bi\s+(?:strongly\s+)?prefer\s+(?:units?\s+of\s+)?{re.escape(better)}\s+(?:over|to|rather than)\s+{re.escape(worse)}\b",
                rf"\bi\s+value\s+{re.escape(better)}\s+(?:more|higher)\s+than\s+{re.escape(worse)}\b",
                rf"\b{re.escape(better)}\s+(?:is|are)?\s*(?:more|higher)[^.,;]{{0,35}}(?:than|over)\s+{re.escape(worse)}\s+to\s+me\b",
            )
            if any(re.search(pattern, text) for pattern in patterns):
                key = ("ranking", better, worse)
                if key not in seen:
                    claims.append(values[better] > values[worse])
                    seen.add(key)

    for resource in resources:
        patterns = (
            rf"\bi\s+value\s+{re.escape(resource)}\s+(?:at|=)\s*\$?(\d+(?:\.\d+)?)",
            rf"\bmy\s+(?:per-unit\s+)?value\s+(?:of|for)\s+{re.escape(resource)}\s*(?:is|=|at)\s*\$?(\d+(?:\.\d+)?)",
            rf"\b{re.escape(resource)}\s+is\s+worth\s*\$?(\d+(?:\.\d+)?)\s+to\s+me\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                key = ("number", resource)
                if key not in seen:
                    claims.append(abs(float(match.group(1)) - values[resource]) < EPSILON)
                    seen.add(key)
                break
    return claims


def summarize_communication(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    """Describe targeted questions and verifiable preference revelation."""
    questions = 0
    answered = 0
    questions_by_episode = 0
    messages = 0
    rate_messages = 0
    claims: list[bool] = []
    truthful_claim_episodes = 0

    for episode in rows:
        turns = valid_turns(episode)
        episode_asked = False
        episode_truthful = False
        for index, turn in enumerate(turns):
            message = _message(turn)
            if message:
                messages += 1
            if _RATE_LANGUAGE.search(message):
                rate_messages += 1
            turn_claims = _preference_claims(
                message, turn["actor"], episode["market"]
            )
            claims.extend(turn_claims)
            episode_truthful = episode_truthful or any(turn_claims)
            if not _is_discovery_question(message):
                continue
            questions += 1
            episode_asked = True
            if index + 1 < len(turns):
                reply = turns[index + 1]
                reply_message = _message(reply)
                reply_claims = _preference_claims(
                    reply_message, reply["actor"], episode["market"]
                )
                informative_text = bool(
                    reply_claims or _RATE_LANGUAGE.search(reply_message)
                )
                behavioral_answer = (
                    reply["action"].get("action") == "propose"
                )
                answered += int(informative_text or behavioral_answer)
        questions_by_episode += int(episode_asked)
        truthful_claim_episodes += int(episode_truthful)

    return {
        "episodes": len(rows),
        "messages": messages,
        "targeted_questions": questions,
        "episodes_asking_rate": _rate(questions_by_episode, len(rows)),
        "substantive_answer_rate": _rate(answered, questions),
        "parseable_preference_claims": len(claims),
        "truthful_preference_claims": sum(claims),
        "truthful_claim_rate": mean([float(claim) for claim in claims]),
        "episodes_with_truthful_preference_revelation_rate": _rate(
            truthful_claim_episodes, len(rows)
        ),
        "quantity_or_rate_messages": rate_messages,
        "quantity_or_rate_message_rate": _rate(rate_messages, messages),
    }


def analyze_coordination_exploitation(
    episodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return condition cells and the exact-versus-ranked decomposition."""
    cells: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        cells[episode["condition"]].append(episode)
    summaries = {}
    for condition in CONDITION_ORDER:
        if not cells[condition]:
            continue
        summaries[condition] = {
            "search": summarize_search_and_bargaining(cells[condition]),
            "counteroffers": summarize_counteroffers(cells[condition]),
            "communication": summarize_communication(cells[condition]),
        }

    decomposition = None
    if "rank_only" in summaries and "exact_values" in summaries:
        ranked = summaries["rank_only"]["search"]
        exact = summaries["exact_values"]["search"]
        search_improvement = (
            exact["mean_best_ir_offer_welfare"]
            - ranked["mean_best_ir_offer_welfare"]
        )
        realization_loss_change = (
            exact["mean_realization_loss"] - ranked["mean_realization_loss"]
        )
        final_change = (
            exact["mean_final_welfare"] - ranked["mean_final_welfare"]
        )
        decomposition = {
            "search_improvement": search_improvement,
            "realization_loss_change": realization_loss_change,
            "final_welfare_change": final_change,
            "identity_residual": final_change
            - (search_improvement - realization_loss_change),
        }
    return {"cells": summaries, "exact_minus_ranked": decomposition}
