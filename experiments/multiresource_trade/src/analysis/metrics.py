"""Cell summaries and paired, profile-clustered information effects."""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any

from ..trading.market_core import extraction_opportunity, first_best_gain


def extraction_group(market: dict[str, Any]) -> tuple[str, float]:
    """Compare own-gain advantages over full exchange, normalized by total gains.

    Construction labels do not always identify the higher-opportunity agent.
    Recompute both opportunities from utilities for the grouped summaries.
    """
    a, b = (
        extraction_opportunity(market, actor)["normalized_appropriation_advantage"]
        for actor in ("A", "B")
    )
    group = "equal" if abs(a - b) < 1e-12 else "a_higher" if a > b else "b_higher"
    return group, max(a, b)


def mean(numbers: list[float]) -> float:
    return sum(numbers) / len(numbers) if numbers else 0.0


def percentile(numbers: list[float], probability: float) -> float:
    """Linearly interpolate one percentile of a nonempty sample."""
    ordered = sorted(numbers)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def clustered_interval(
    differences: list[dict[str, Any]],
    field: str,
    draws: int = 50_000,
) -> list[float] | None:
    """Bootstrap profiles while retaining orientations and replications."""
    clusters: dict[str, list[float]] = defaultdict(list)
    for row in differences:
        clusters[row["profile"]].append(row[field])
    if len(clusters) < 2:
        return None

    generator = random.Random(20260820)
    names = sorted(clusters)
    estimates = []
    for _ in range(draws):
        selected = [generator.choice(names) for _ in names]
        estimates.append(
            mean([value for name in selected for value in clusters[name]])
        )
    return [percentile(estimates, 0.025), percentile(estimates, 0.975)]


def analyze(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize cells and matched effects relative to ranking information."""
    cells: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    by_id = {}
    for episode in episodes:
        cells[(episode["max_messages"], episode["condition"])].append(episode)
        pair_key = (
            episode["market"]["market_id"],
            episode["max_messages"],
            episode["replication"],
            episode["first_actor"],
            episode["condition"],
        )
        by_id[pair_key] = episode

    cell_results = {
        f"h{horizon}_{condition}": summarize_cell(rows)
        for (horizon, condition), rows in sorted(cells.items())
    }

    effects = {}
    region_effects = {}
    comparisons = sorted({row["condition"] for row in episodes} - {"rank_only"})
    for horizon in sorted({row["max_messages"] for row in episodes}):
        for condition in comparisons:
            differences = paired_differences(
                episodes, by_id, horizon, condition
            )
            profile_means = [
                mean([
                    row["welfare"]
                    for row in differences
                    if row["profile"] == profile
                ])
                for profile in sorted({row["profile"] for row in differences})
            ]
            effects[f"h{horizon}_{condition}_minus_rank_only"] = {
                "matched_pairs": len(differences),
                "profile_clusters": len(profile_means),
                "mean": mean([row["welfare"] for row in differences]),
                "profile_cluster_bootstrap_95_ci": clustered_interval(
                    differences, "welfare"
                ),
                "mean_normalized_gain_a_change": mean([
                    row["gain_a"] for row in differences
                ]),
                "mean_normalized_gain_b_change": mean([
                    row["gain_b"] for row in differences
                ]),
                "profiles_negative": sum(value < 0 for value in profile_means),
                "profiles_positive": sum(value > 0 for value in profile_means),
            }
            region_rows = [
                row for row in differences if row["extraction_role"] is not None
            ]
            if region_rows:
                region_effects[f"h{horizon}_{condition}_minus_rank_only"] = {
                    "by_role": grouped_region_effects(
                        region_rows, "extraction_role"
                    ),
                    "by_abundant_quantity": grouped_region_effects(
                        region_rows, "abundant_quantity"
                    ),
                    "matched_market_effects": region_rows,
                }
    result = {"cells": cell_results, "paired_effects": effects}
    if region_effects:
        result["extraction_region_effects"] = region_effects
    return result


def grouped_region_effects(
    differences: list[dict[str, Any]], field: str
) -> dict[str, dict[str, float | int]]:
    """Summarize a phase-diagram axis without discarding matched rows."""
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in differences:
        groups[row[field]].append(row)
    return {
        str(group): {
            "matched_pairs": len(rows),
            "mean_welfare_change": mean([row["welfare"] for row in rows]),
            "mean_extraction_pressure": mean([
                row["extraction_pressure"] for row in rows
            ]),
        }
        for group, rows in sorted(groups.items(), key=lambda item: str(item[0]))
    }


def summarize_cell(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one horizon-by-information cell."""
    invalid_counts = [sum(bool(turn.get("errors")) for turn in row["transcript"])
                      for row in rows]
    return {
        "episodes": len(rows),
        "invalid_actions": sum(invalid_counts),
        "episodes_with_invalid_actions": sum(count > 0 for count in invalid_counts),
        "welfare": mean([row["outcome"]["normalized_welfare"] for row in rows]),
        "agreement_rate": mean([
            float(row["outcome"]["agreement"]) for row in rows
        ]),
        "ir_agreement_rate": mean([
            float(row["outcome"]["individually_rational"]) for row in rows
        ]),
        "normalized_gain_a": mean([
            row["outcome"]["gain_a"] / first_best_gain(row["market"])
            for row in rows
        ]),
        "normalized_gain_b": mean([
            row["outcome"]["gain_b"] / first_best_gain(row["market"])
            for row in rows
        ]),
    }


def paired_differences(
    episodes: list[dict[str, Any]],
    by_id: dict[tuple[Any, ...], dict[str, Any]],
    horizon: int,
    condition: str,
) -> list[dict[str, Any]]:
    """Match one condition to ranking within market, mover, and replication."""
    differences = []
    for ranked in episodes:
        if (
            ranked["max_messages"] != horizon
            or ranked["condition"] != "rank_only"
        ):
            continue
        key = (
            ranked["market"]["market_id"],
            horizon,
            ranked["replication"],
            ranked["first_actor"],
            condition,
        )
        comparison = by_id.get(key)
        if comparison is None:
            continue
        market = ranked["market"]
        scale = first_best_gain(market)
        role, pressure = (
            extraction_group(market)
            if "extraction_role" in market
            else (None, 0.0)
        )
        differences.append({
            "market_id": ranked["market"]["market_id"],
            "profile": ranked["market"].get(
                "type_pair", ranked["market"]["market_id"]
            ),
            "extraction_role": role,
            "construction_extraction_role": market.get("extraction_role"),
            "extraction_pressure": pressure,
            "abundant_quantity": (
                max(ranked["market"]["inventory_a"].values())
                if "extraction_role" in ranked["market"]
                else None
            ),
            "replication": ranked["replication"],
            "welfare": (
                comparison["outcome"]["normalized_welfare"]
                - ranked["outcome"]["normalized_welfare"]
            ),
            "gain_a": (
                comparison["outcome"]["gain_a"]
                - ranked["outcome"]["gain_a"]
            ) / scale,
            "gain_b": (
                comparison["outcome"]["gain_b"]
                - ranked["outcome"]["gain_b"]
            ) / scale,
        })
    return differences


def analyze_mediator(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize mediated cells and every matched disclosure contrast.

    Mediated episodes have one proposal rather than a dialogue horizon, so
    keeping this analysis separate prevents an accidental peer/mediator pool.
    """
    cells: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_id = {}
    for episode in episodes:
        cells[episode["condition"]].append(episode)
        by_id[
            (
                episode["market"]["market_id"],
                episode["replication"],
                episode["condition"],
            )
        ] = episode

    cell_results = {
        condition: summarize_mediator_cell(rows)
        for condition, rows in sorted(cells.items())
    }
    comparisons = (
        ("rank_only", "inventory_only"),
        ("exact_values", "inventory_only"),
        ("exact_values", "rank_only"),
    )
    effects = {}
    for treatment, baseline in comparisons:
        differences = []
        for episode in episodes:
            if episode["condition"] != baseline:
                continue
            comparison = by_id.get((
                episode["market"]["market_id"],
                episode["replication"],
                treatment,
            ))
            if comparison is None:
                continue
            scale = first_best_gain(episode["market"])
            differences.append({
                "profile": episode["market"].get(
                    "type_pair", episode["market"]["market_id"]
                ),
                "welfare": (
                    comparison["outcome"]["normalized_welfare"]
                    - episode["outcome"]["normalized_welfare"]
                ),
                "gain_a": (
                    comparison["outcome"]["gain_a"]
                    - episode["outcome"]["gain_a"]
                ) / scale,
                "gain_b": (
                    comparison["outcome"]["gain_b"]
                    - episode["outcome"]["gain_b"]
                ) / scale,
            })
        profile_means = [
            mean([
                row["welfare"]
                for row in differences
                if row["profile"] == profile
            ])
            for profile in sorted({row["profile"] for row in differences})
        ]
        effects[f"{treatment}_minus_{baseline}"] = {
            "matched_pairs": len(differences),
            "profile_clusters": len(profile_means),
            "mean": mean([row["welfare"] for row in differences]),
            "profile_cluster_bootstrap_95_ci": clustered_interval(
                differences, "welfare"
            ),
            "mean_normalized_gain_a_change": mean([
                row["gain_a"] for row in differences
            ]),
            "mean_normalized_gain_b_change": mean([
                row["gain_b"] for row in differences
            ]),
            "profiles_negative": sum(value < 0 for value in profile_means),
            "profiles_positive": sum(value > 0 for value in profile_means),
        }
    mechanisms = {episode.get("mechanism", "mediator") for episode in episodes}
    mechanism = next(iter(mechanisms)) if len(mechanisms) == 1 else "mixed_mediator"
    return {
        "mechanism": mechanism,
        "cells": cell_results,
        "paired_effects": effects,
    }


def summarize_mediator_cell(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Add proposal-quality diagnostics to the common welfare summary."""
    summary = summarize_cell(rows)
    proposals = [
        row for row in rows
        if row.get("proposal") is not None
        and row["proposal"].get("action") == "propose"
    ]
    summary.update({
        "valid_proposal_rate": len(proposals) / len(rows) if rows else 0.0,
        "proposed_ir_rate": mean([
            float(row["proposed_outcome"]["individually_rational"])
            for row in proposals
        ]),
        "mean_proposed_normalized_welfare": mean([
            row["proposed_outcome"]["normalized_welfare"]
            for row in proposals
        ]),
        "acceptance_given_valid_proposal": mean([
            float(row["outcome"]["agreement"])
            for row in proposals
        ]),
    })
    if any(row.get("mechanism") == "feedback_mediator" for row in rows):
        revision_rows = [
            row for row in rows if row.get("revision_prompt") is not None
        ]
        feedback_rows = [
            feedback
            for row in rows
            for feedback in row.get("feedback", [])
        ]
        summary.update({
            "initial_valid_proposal_rate": mean([
                float(row.get("initial_proposal") is not None)
                for row in rows
            ]),
            "valid_feedback_rate": mean([
                float(feedback.get("feedback") is not None)
                for feedback in feedback_rows
            ]),
            "initial_joint_acceptance_rate": mean([
                float(row.get("termination") == "accepted_initial")
                for row in rows
            ]),
            "revision_rate": len(revision_rows) / len(rows) if rows else 0.0,
            "revised_acceptance_given_revision": mean([
                float(row.get("termination") == "accepted_revised")
                for row in revision_rows
            ]),
        })
    if any(row.get("mechanism") == "consulted_mediator" for row in rows):
        consultation_rows = [
            consultation
            for row in rows
            for consultation in row.get("consultations", [])
        ]
        summary["valid_consultation_rate"] = mean([
            float(consultation.get("consultation") is not None)
            for consultation in consultation_rows
        ])
    return summary
