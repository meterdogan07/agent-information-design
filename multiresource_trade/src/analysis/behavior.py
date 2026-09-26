"""Behavioral summaries for saved peer-to-peer negotiation episodes.

The functions here read completed episode dictionaries and never call a model.
Proposal and message statistics include every episode. Agreement-time and
surplus-share statistics condition on agreement, as their names state.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from ..trading.markets import first_best_gain, gains
from .metrics import mean, percentile


CONDITION_ORDER = ("inventory_only", "rank_only", "exact_values")


def valid_turns(episode: dict[str, Any]) -> list[dict[str, Any]]:
    """Return parsed game turns, excluding failed parse/retry records."""
    return [row for row in episode["transcript"] if row.get("action")]


def proposal_turns(episode: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row for row in valid_turns(episode)
        if row["action"].get("action") == "propose"
    ]


def score_offer(episode: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
    """Score one proposed trade from its maker's perspective."""
    gain_a, gain_b = gains(episode["market"], turn["action"])
    scale = first_best_gain(episode["market"])
    actor = turn["actor"]
    own, other = (gain_a, gain_b) if actor == "A" else (gain_b, gain_a)
    return {
        "actor": actor,
        "role": "proposer" if actor == episode["first_actor"] else "responder",
        "own_gain": own / scale,
        "other_gain": other / scale,
        "welfare": (gain_a + gain_b) / scale,
        "ir": gain_a > 0 and gain_b > 0,
        "proposal_id": turn["action"].get("proposal_id"),
    }


def episode_offers(episode: dict[str, Any]) -> list[dict[str, Any]]:
    accepted_id = (episode.get("agreement") or {}).get("proposal_id")
    offers = []
    for message_number, turn in enumerate(valid_turns(episode), start=1):
        if turn["action"].get("action") != "propose":
            continue
        offer = score_offer(episode, turn)
        offer["message_number"] = message_number
        offer["accepted"] = bool(accepted_id) and offer["proposal_id"] == accepted_id
        offers.append(offer)
    return offers


def exchanged_units(episode: dict[str, Any]) -> int:
    """Count units transferred in the accepted proposal, both directions."""
    agreement = episode.get("agreement") or {}
    return sum((agreement.get("a_gives") or {}).values()) + sum(
        (agreement.get("b_gives") or {}).values()
    )


def first_proposal_accepted_immediately(episode: dict[str, Any]) -> bool:
    """Whether the first two valid actions are proposal then acceptance."""
    turns = valid_turns(episode)
    return bool(
        episode["outcome"]["agreement"]
        and len(turns) == 2
        and turns[0]["action"].get("action") == "propose"
        and turns[1]["action"].get("action") == "accept"
    )


def proposer_surplus_share(episode: dict[str, Any]) -> float | None:
    """First mover's share of realized surplus, or None without a deal."""
    if not episode["outcome"]["agreement"]:
        return None
    gains = {
        "A": episode["outcome"]["gain_a"],
        "B": episode["outcome"]["gain_b"],
    }
    total = gains["A"] + gains["B"]
    return gains[episode["first_actor"]] / total if total > 0 else None


def summarize_behavior(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    """Summarize one model-by-disclosure cell.

    Proposer/responder mean first and second mover, rather than fixed agent
    letters. This remains valid if a future panel rotates the first mover.
    """
    agreements = [row for row in rows if row["outcome"]["agreement"]]
    messages = [len(valid_turns(row)) for row in agreements]
    proposal_counts = [len(proposal_turns(row)) for row in rows]

    proposer_gains = []
    responder_gains = []
    proposer_shares = []
    responder_shares = []
    proposer_proposals = []
    responder_proposals = []
    for row in rows:
        scale = first_best_gain(row["market"])
        first = row["first_actor"]
        second = "B" if first == "A" else "A"
        gains = {
            "A": row["outcome"]["gain_a"],
            "B": row["outcome"]["gain_b"],
        }
        proposer_gains.append(gains[first] / scale)
        responder_gains.append(gains[second] / scale)
        proposals = proposal_turns(row)
        proposer_proposals.append(sum(turn["actor"] == first for turn in proposals))
        responder_proposals.append(sum(turn["actor"] == second for turn in proposals))
        share = proposer_surplus_share(row)
        if share is not None:
            proposer_shares.append(share)
            responder_shares.append(1 - share)

    horizons_used = [
        count / row["max_messages"]
        for row, count in zip(agreements, messages)
    ]
    return {
        "episodes": len(rows),
        "welfare": mean([row["outcome"]["normalized_welfare"] for row in rows]),
        "agreement_rate": len(agreements) / len(rows),
        "disagreement_rate": 1 - len(agreements) / len(rows),
        "mean_proposals": mean(proposal_counts),
        "mean_counteroffers": mean([max(count - 1, 0) for count in proposal_counts]),
        "mean_proposer_proposals": mean(proposer_proposals),
        "mean_responder_proposals": mean(responder_proposals),
        "mean_messages_to_agreement": mean(messages),
        "median_messages_to_agreement": median(messages) if messages else 0.0,
        "p90_messages_to_agreement": percentile(messages, 0.90) if messages else 0.0,
        "mean_horizon_fraction_on_agreement": mean(horizons_used),
        "first_proposal_acceptance_rate": mean([
            float(first_proposal_accepted_immediately(row)) for row in rows
        ]),
        "first_best_rate": mean([
            float(abs(row["outcome"]["normalized_welfare"] - 1.0) < 1e-9)
            for row in rows
        ]),
        "mean_exchanged_units": mean([exchanged_units(row) for row in rows]),
        "normalized_proposer_gain": mean(proposer_gains),
        "normalized_responder_gain": mean(responder_gains),
        "proposer_surplus_share_on_agreement": mean(proposer_shares),
        "responder_surplus_share_on_agreement": mean(responder_shares),
    }


def analyze_behavior(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Return disclosure-cell summaries and exact-minus-ranked changes."""
    cells: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        cells[episode["condition"]].append(episode)
    summaries = {
        condition: summarize_behavior(cells[condition])
        for condition in CONDITION_ORDER
        if cells[condition]
    }

    ranked = summaries.get("rank_only")
    exact = summaries.get("exact_values")
    changes = None
    if ranked and exact:
        by_key = {
            (
                episode["market"]["market_id"],
                episode.get("replication", 0),
                episode["max_messages"],
                episode["first_actor"],
                episode["condition"],
            ): episode
            for episode in episodes
        }
        paired_share_changes = []
        for episode in cells["rank_only"]:
            exact_episode = by_key.get((
                episode["market"]["market_id"],
                episode.get("replication", 0),
                episode["max_messages"],
                episode["first_actor"],
                "exact_values",
            ))
            ranked_share = proposer_surplus_share(episode)
            exact_share = (
                proposer_surplus_share(exact_episode) if exact_episode else None
            )
            if ranked_share is not None and exact_share is not None:
                paired_share_changes.append(exact_share - ranked_share)
        changes = {
            "welfare": exact["welfare"] - ranked["welfare"],
            "agreement_rate": exact["agreement_rate"] - ranked["agreement_rate"],
            "mean_proposals": exact["mean_proposals"] - ranked["mean_proposals"],
            "mean_messages_to_agreement": (
                exact["mean_messages_to_agreement"]
                - ranked["mean_messages_to_agreement"]
            ),
            "normalized_proposer_gain": (
                exact["normalized_proposer_gain"]
                - ranked["normalized_proposer_gain"]
            ),
            "normalized_responder_gain": (
                exact["normalized_responder_gain"]
                - ranked["normalized_responder_gain"]
            ),
            "proposer_surplus_share": mean(paired_share_changes),
            "paired_surplus_share_episodes": len(paired_share_changes),
        }
    return {"cells": summaries, "exact_minus_ranked": changes}


def summarize_offer_quality(
    rows: list[dict[str, Any]], high_welfare: float = 0.90
) -> dict[str, float | int]:
    """Separate the quality of proposals from acceptance of those proposals."""
    offers = []
    rejected_better_episodes = 0
    best_ir_welfare = []
    for episode in rows:
        episode_offers_scored = episode_offers(episode)
        offers.extend(episode_offers_scored)
        ir_offers = [offer for offer in episode_offers_scored if offer["ir"]]
        best_ir_welfare.append(
            max([offer["welfare"] for offer in ir_offers], default=0.0)
        )
        final = episode["outcome"]["normalized_welfare"]
        if any(
            offer["ir"]
            and not offer["accepted"]
            and offer["welfare"] > final + 1e-9
            for offer in episode_offers_scored
        ):
            rejected_better_episodes += 1

    ir_offers = [offer for offer in offers if offer["ir"]]
    high_offers = [
        offer for offer in ir_offers if offer["welfare"] >= high_welfare
    ]
    first_best_offers = [
        offer for offer in ir_offers if abs(offer["welfare"] - 1.0) < 1e-9
    ]
    result: dict[str, float | int] = {
        "episodes": len(rows),
        "offers": len(offers),
        "ir_offers": len(ir_offers),
        "high_welfare_offers": len(high_offers),
        "high_welfare_rejected": sum(not offer["accepted"] for offer in high_offers),
        "high_welfare_rejection_rate": mean([
            float(not offer["accepted"]) for offer in high_offers
        ]),
        "first_best_offers": len(first_best_offers),
        "first_best_accepted": sum(offer["accepted"] for offer in first_best_offers),
        "episodes_rejecting_better_ir_offer": rejected_better_episodes,
        "mean_best_ir_offer_welfare": mean(best_ir_welfare),
        "mean_final_welfare": mean([
            episode["outcome"]["normalized_welfare"] for episode in rows
        ]),
    }
    result["mean_realization_loss"] = (
        result["mean_best_ir_offer_welfare"] - result["mean_final_welfare"]
    )
    for role in ("proposer", "responder"):
        role_offers = [offer for offer in offers if offer["role"] == role]
        result[f"{role}_offers"] = len(role_offers)
        result[f"{role}_mean_offer_welfare"] = mean([
            offer["welfare"] for offer in role_offers
        ])
        result[f"{role}_mean_own_gain"] = mean([
            offer["own_gain"] for offer in role_offers
        ])
        result[f"{role}_mean_other_gain"] = mean([
            offer["other_gain"] for offer in role_offers
        ])
    return result
