"""Build figures from completed bargaining experiments.

This script is offline: it makes no model calls and reads only the immutable,
audit-passing profitable-market panel.  It recomputes every plotted metric from
saved market states and engine-validated events rather than copying values from
earlier reports.

Run from the repository root:

    cd experiments/single_good_bargaining
    python -m src.figures
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

from experiment_runtime import read_jsonl


HERE = Path(__file__).resolve().parent
RESULT_ROOT = (
    HERE.parent
    / "results/five_per_profitable_stratum_both_starters_v1/seed20260824_n30"
)
OUTPUT_ROOT = HERE.parent / "output"

MODELS = {
    "gpt5mini": "GPT-5 mini",
    "gpt51": "GPT-5.1",
    "gemini": "Gemini 3.7 Flash",
    "claudesonnet5": "Claude Sonnet 5",
    "grok43": "Grok 4.3",
}
HORIZONS = (2, 4, 6)
PRIMARY_CONDITIONS = ("hidden", "coarse", "exact")
ALL_CONDITIONS = (
    "hidden",
    "coarse",
    "exact",
    "seller_informed",
    "buyer_informed",
)
CONDITION_LABELS = {
    "hidden": "Least information",
    "coarse": "Coarse information",
    "exact": "Full information",
    "seller_informed": "Seller informed",
    "buyer_informed": "Buyer informed",
}
INFO_COLORS = {
    "hidden": "#8B8F94",
    "coarse": "#3E668C",
    "exact": "#B6683C",
}
MODEL_COLORS = {
    "gpt5mini": "#3E668C",
    "gpt51": "#B6683C",
    "gemini": "#2F7B73",
    "claudesonnet5": "#756395",
    "grok43": "#9B5057",
}
DIAGNOSTIC_COLORS = {
    "candidate": "#2F7B73",
    "realization": "#B6683C",
    "outcome": "#2D3033",
}
TEXT_COLOR = "#26282A"
AXIS_COLOR = "#555A5E"
GRID_COLOR = "#D9DCDE"
DELTA_CMAP = mpl.colors.LinearSegmentedColormap.from_list(
    "information_delta",
    [INFO_COLORS["coarse"], "#F5F4F1", INFO_COLORS["exact"]],
)

BOOTSTRAP_REPS = 4_000
BOOTSTRAP_SEED = 20260901


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def nash_score(price: float | None, cost: float, value: float) -> float:
    """Normalized Nash product of gains relative to disagreement.

    The score is zero without an individually rational price and one at the
    equal-surplus split.  It is computed after the interaction and is never
    shown to an agent.
    """
    surplus = value - cost
    if price is None or surplus <= 0 or not cost <= price <= value:
        return 0.0
    return float(4.0 * (price - cost) * (value - price) / (surplus * surplus))


def best_proposed_nash(episode: dict) -> float:
    market = episode["market"]
    cost = float(market["seller_cost"])
    value = float(market["buyer_value"])
    scores = [
        nash_score(float(event["price"]), cost, value)
        for event in episode["outcome"].get("events", [])
        if event.get("valid")
        and event.get("decision") == "propose"
        and event.get("price") is not None
        and cost <= float(event["price"]) <= value
    ]
    return max(scores, default=0.0)


def derive_episode(episode: dict, model: str, horizon: int) -> dict:
    market = episode["market"]
    metrics = episode["metrics"]
    outcome = episode["outcome"]
    potential = float(market["buyer_value"] - market["seller_cost"])
    agreement = float(bool(outcome.get("executed_trade")))
    accepted_price = outcome.get("accepted_price") if agreement else None
    realized_nash = nash_score(
        float(accepted_price) if accepted_price is not None else None,
        float(market["seller_cost"]),
        float(market["buyer_value"]),
    )
    candidate_nash = best_proposed_nash(episode)
    seller_fraction = float(metrics["seller_gain"]) / potential if potential else 0.0
    buyer_fraction = float(metrics["buyer_gain"]) / potential if potential else 0.0
    return {
        "model": model,
        "model_label": MODELS[model],
        "horizon": horizon,
        "condition": episode["condition"],
        "market_id": market["market_id"],
        "base_market_id": market["base_market_id"],
        "family": market["family"],
        "first_mover": market["first_mover"],
        "agreement": agreement,
        "normalized_welfare": float(metrics["normalized_welfare"]),
        "potential_surplus": potential,
        "accepted_price": accepted_price,
        "seller_share": metrics.get("seller_surplus_share"),
        "buyer_share": metrics.get("buyer_surplus_share"),
        "seller_realized_fraction": seller_fraction,
        "buyer_realized_fraction": buyer_fraction,
        "nash_score": realized_nash,
        "candidate_nash": candidate_nash,
        "realization_loss": candidate_nash - realized_nash,
        "turns_used": float(metrics["turns_used"]),
        "proposal_count": float(metrics["proposal_count"]),
        "endpoint_offer": float(bool(metrics["ever_endpoint_offer"])),
    }


def load_primary(root: Path) -> list[dict]:
    rows: list[dict] = []
    for horizon in HORIZONS:
        for model in MODELS:
            run = root / f"h{horizon}" / model
            audit = read_json(run / "audit.json")
            if not audit.get("passed") or not audit.get("frozen_source_verified"):
                raise ValueError(f"Unverified result panel: {run}")
            episodes = read_jsonl(run / "episodes.jsonl")
            if len(episodes) != 30 * len(ALL_CONDITIONS):
                raise ValueError(f"Expected 150 episodes in {run}; found {len(episodes)}")
            rows.extend(derive_episode(episode, model, horizon) for episode in episodes)
    return rows


def validate_primary(rows: list[dict]) -> None:
    """Check the assumptions used by every figure metric."""
    expected = 30 * len(MODELS) * len(HORIZONS) * len(ALL_CONDITIONS)
    if len(rows) != expected:
        raise ValueError(f"Expected {expected:,} primary episodes; found {len(rows)}")
    for model in MODELS:
        for horizon in HORIZONS:
            for condition in ALL_CONDITIONS:
                cell = select(rows, model=model, horizon=horizon, condition=condition)
                if len(cell) != 30:
                    raise ValueError(
                        f"Expected 30 episodes for {model}/H{horizon}/{condition}; "
                        f"found {len(cell)}"
                    )
                family_counts = {
                    family: len(select(cell, family=family))
                    for family in ("thin", "medium", "large")
                }
                if set(family_counts.values()) != {10}:
                    raise ValueError(f"Unbalanced strata in {model}/H{horizon}/{condition}")
    for row in rows:
        if row["potential_surplus"] <= 0:
            raise ValueError("The main panel must contain profitable markets only")
        if not np.isclose(row["normalized_welfare"], row["agreement"]):
            raise ValueError("Normalized welfare must equal execution in profitable markets")
        if not 0 <= row["nash_score"] <= 1 + 1e-10:
            raise ValueError("Nash score outside [0, 1]")
        if not 0 <= row["candidate_nash"] <= 1 + 1e-10:
            raise ValueError("Candidate Nash score outside [0, 1]")
        if row["realization_loss"] < -1e-10:
            raise ValueError("Accepted outcome exceeds the best proposal in its transcript")
        if row["agreement"]:
            if row["seller_share"] is None or row["buyer_share"] is None:
                raise ValueError("Executed agreement lacks surplus shares")
            if not np.isclose(row["seller_share"] + row["buyer_share"], 1.0):
                raise ValueError("Executed surplus shares do not sum to one")


def select(rows: Iterable[dict], **filters) -> list[dict]:
    return [
        row for row in rows
        if all(row.get(name) == value for name, value in filters.items())
    ]


def safe_mean(values: Iterable[float | None]) -> float:
    clean = [float(value) for value in values if value is not None]
    return float(np.mean(clean)) if clean else float("nan")


def cluster_interval(
    rows: list[dict], metric: str | Callable[[dict], float | None]
) -> tuple[float, float, float]:
    """Mean and 95% cluster bootstrap interval over mirrored base markets."""
    value_fn = metric if callable(metric) else lambda row: row.get(metric)
    by_cluster: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = value_fn(row)
        if value is not None and np.isfinite(float(value)):
            by_cluster[row["base_market_id"]].append(float(value))
    cluster_ids = sorted(by_cluster)
    observed = safe_mean(value for values in by_cluster.values() for value in values)
    if not cluster_ids:
        return observed, float("nan"), float("nan")
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws = np.empty(BOOTSTRAP_REPS)
    for index in range(BOOTSTRAP_REPS):
        sampled = rng.choice(cluster_ids, size=len(cluster_ids), replace=True)
        draws[index] = safe_mean(
            value for cluster in sampled for value in by_cluster[str(cluster)]
        )
    low, high = np.quantile(draws, [0.025, 0.975])
    return observed, float(low), float(high)


def paired_rows(
    rows: list[dict], high: str, low: str, metric: str, **filters
) -> list[dict]:
    subset = select(rows, **filters)
    low_by_market = {
        row["market_id"]: row for row in subset if row["condition"] == low
    }
    differences = []
    for row in subset:
        if row["condition"] != high or row["market_id"] not in low_by_market:
            continue
        baseline = low_by_market[row["market_id"]]
        differences.append({
            "base_market_id": row["base_market_id"],
            "difference": float(row[metric]) - float(baseline[metric]),
        })
    return differences


def paired_interval(
    rows: list[dict], high: str, low: str, metric: str, **filters
) -> tuple[float, float, float]:
    return cluster_interval(
        paired_rows(rows, high, low, metric, **filters), "difference"
    )


def set_style() -> None:
    mpl.rcParams.update({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "STIXGeneral"],
        "mathtext.fontset": "stix",
        "font.size": 8.5,
        "axes.titlesize": 9,
        "axes.labelsize": 8.5,
        "legend.fontsize": 7.4,
        "xtick.labelsize": 7.5,
        "ytick.labelsize": 7.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.72,
        "axes.edgecolor": AXIS_COLOR,
        "axes.labelcolor": TEXT_COLOR,
        "axes.titlecolor": TEXT_COLOR,
        "xtick.color": TEXT_COLOR,
        "ytick.color": TEXT_COLOR,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "figure.dpi": 180,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
    })


def finish_axes(ax: plt.Axes, grid_axis: str | None = "y") -> None:
    ax.spines["left"].set_color(AXIS_COLOR)
    ax.spines["bottom"].set_color(AXIS_COLOR)
    ax.tick_params(width=0.65, length=2.8)
    if grid_axis:
        ax.grid(axis=grid_axis, color=GRID_COLOR, lw=0.5, alpha=0.82)
        ax.set_axisbelow(True)


def save_figure(fig: plt.Figure, output: Path, stem: str, atlas: PdfPages) -> None:
    fig.savefig(output / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(output / f"{stem}.svg", bbox_inches="tight")
    fig.savefig(output / f"{stem}.png", dpi=300, bbox_inches="tight")
    atlas.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def information_and_nash(rows: list[dict]) -> plt.Figure:
    fig, axes = plt.subplots(2, len(MODELS), figsize=(7.15, 4.25), sharex=True, sharey="row")
    for column, model in enumerate(MODELS):
        for condition in PRIMARY_CONDITIONS:
            agreement, agreement_low, agreement_high = [], [], []
            nash, nash_low, nash_high = [], [], []
            for horizon in HORIZONS:
                cell = select(rows, model=model, horizon=horizon, condition=condition)
                estimate = cluster_interval(cell, "agreement")
                agreement.append(estimate[0]); agreement_low.append(estimate[1]); agreement_high.append(estimate[2])
                # Agreement and division are distinct outcomes. Conditioning
                # on executed trades prevents disagreement from being counted
                # a second time in the balance panel.
                agreed = [row for row in cell if row["agreement"]]
                estimate = cluster_interval(agreed, "nash_score")
                nash.append(estimate[0]); nash_low.append(estimate[1]); nash_high.append(estimate[2])
            color = INFO_COLORS[condition]
            label = CONDITION_LABELS[condition]
            for ax, values, lows, highs in (
                (axes[0, column], agreement, agreement_low, agreement_high),
                (axes[1, column], nash, nash_low, nash_high),
            ):
                errors = np.vstack((np.array(values) - lows, np.array(highs) - values))
                ax.errorbar(
                    HORIZONS, values, yerr=errors, color=color, marker="o",
                    ms=3.2, lw=1.15, capsize=1.8, label=label,
                )
        axes[0, column].set_title(MODELS[model])
        axes[1, column].set_xticks(HORIZONS, [f"H{h}" for h in HORIZONS])
        axes[0, column].set_ylim(-0.03, 1.05)
        axes[1, column].set_ylim(-0.03, 1.05)
        finish_axes(axes[0, column])
        finish_axes(axes[1, column])
    axes[0, 0].set_ylabel("Agreement rate")
    axes[1, 0].set_ylabel("Nash balance\namong agreements")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, ncol=3, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.01))
    fig.subplots_adjust(top=0.84, hspace=0.22, wspace=0.18)
    return fig


def nash_decomposition(rows: list[dict]) -> plt.Figure:
    fig, axes = plt.subplots(1, len(MODELS), figsize=(7.15, 2.45), sharey=True)
    x = np.arange(len(HORIZONS))
    width = 0.26
    for column, model in enumerate(MODELS):
        ax = axes[column]
        delta_q, delta_q_err = [], []
        negative_delta_l, negative_delta_l_err = [], []
        delta_j, delta_j_err = [], []
        for horizon in HORIZONS:
            q = paired_interval(rows, "exact", "coarse", "candidate_nash", model=model, horizon=horizon)
            loss = paired_interval(rows, "exact", "coarse", "realization_loss", model=model, horizon=horizon)
            objective = paired_interval(rows, "exact", "coarse", "nash_score", model=model, horizon=horizon)
            delta_q.append(q[0]); delta_q_err.append((q[0] - q[1], q[2] - q[0]))
            negative_delta_l.append(-loss[0]); negative_delta_l_err.append((loss[2] - loss[0], loss[0] - loss[1]))
            delta_j.append(objective[0]); delta_j_err.append((objective[0] - objective[1], objective[2] - objective[0]))
        ax.bar(x - width / 2, delta_q, width, color=DIAGNOSTIC_COLORS["candidate"], label=r"Candidate gain $\Delta Q$")
        ax.errorbar(x - width / 2, delta_q, yerr=np.array(delta_q_err).T, fmt="none", ecolor="#24554F", capsize=1.7, lw=0.7)
        ax.bar(x + width / 2, negative_delta_l, width, color=DIAGNOSTIC_COLORS["realization"], label=r"Realization contribution $-\Delta L$")
        ax.errorbar(x + width / 2, negative_delta_l, yerr=np.array(negative_delta_l_err).T, fmt="none", ecolor="#70452C", capsize=1.7, lw=0.7)
        ax.errorbar(x, delta_j, yerr=np.array(delta_j_err).T, fmt="D", color=DIAGNOSTIC_COLORS["outcome"], ms=3.2, capsize=1.8, label=r"Final effect $\Delta W$")
        ax.axhline(0, color=AXIS_COLOR, lw=0.7)
        ax.set_title(MODELS[model])
        ax.set_xticks(x, [f"H{h}" for h in HORIZONS])
        finish_axes(ax)
    axes[0].set_ylabel("Full minus coarse")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, ncol=3, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.03))
    fig.subplots_adjust(top=0.79, wspace=0.20)
    return fig


def surplus_division(rows: list[dict]) -> plt.Figure:
    starters = ("seller", "buyer")
    fig, axes = plt.subplots(2, len(MODELS), figsize=(7.15, 4.15), sharex=True, sharey=True)
    for row_index, starter in enumerate(starters):
        for column, model in enumerate(MODELS):
            ax = axes[row_index, column]
            for condition in PRIMARY_CONDITIONS:
                values, lows, highs = [], [], []
                for horizon in HORIZONS:
                    cell = [
                        item for item in select(
                            rows, model=model, horizon=horizon,
                            condition=condition, first_mover=starter,
                        )
                        if item["seller_share"] is not None
                    ]
                    estimate = cluster_interval(cell, "seller_share")
                    values.append(estimate[0]); lows.append(estimate[1]); highs.append(estimate[2])
                errors = np.vstack((np.array(values) - lows, np.array(highs) - values))
                ax.errorbar(
                    HORIZONS, values, yerr=errors, color=INFO_COLORS[condition],
                    marker="o", ms=3.0, lw=1.1, capsize=1.6,
                    label=CONDITION_LABELS[condition],
                )
            ax.axhline(0.5, color=AXIS_COLOR, lw=0.65, ls="--")
            ax.set_ylim(-0.03, 1.03)
            finish_axes(ax)
            if row_index == 0:
                ax.set_title(MODELS[model])
            else:
                ax.set_xticks(HORIZONS, [f"H{h}" for h in HORIZONS])
        axes[row_index, 0].set_ylabel(
            f"Seller share\n({starter}-first markets)"
        )
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, ncol=3, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.01))
    fig.subplots_adjust(top=0.84, hspace=0.22, wspace=0.18)
    return fig


def asymmetric_placement(rows: list[dict]) -> plt.Figure:
    treatments = (("seller_informed", "seller"), ("buyer_informed", "buyer"))
    fig, axes = plt.subplots(2, len(MODELS), figsize=(7.15, 4.0), sharex=True, sharey=True)
    for row_index, (condition, informed_role) in enumerate(treatments):
        payoff_metric = f"{informed_role}_realized_fraction"
        for column, model in enumerate(MODELS):
            ax = axes[row_index, column]
            for position, linestyle, marker in (
                ("first", "-", "o"), ("second", "--", "s")
            ):
                values, lows, highs = [], [], []
                for horizon in HORIZONS:
                    first_mover = informed_role if position == "first" else (
                        "buyer" if informed_role == "seller" else "seller"
                    )
                    estimate = paired_interval(
                        rows, condition, "coarse", payoff_metric,
                        model=model, horizon=horizon, first_mover=first_mover,
                    )
                    values.append(estimate[0]); lows.append(estimate[1]); highs.append(estimate[2])
                errors = np.vstack((np.array(values) - lows, np.array(highs) - values))
                ax.errorbar(
                    HORIZONS, values, yerr=errors, color=MODEL_COLORS[model],
                    marker=marker, ls=linestyle, ms=3.0, lw=1.1, capsize=1.6,
                    label=f"Informed role moves {position}",
                )
            ax.axhline(0, color=AXIS_COLOR, lw=0.65)
            finish_axes(ax)
            if row_index == 0:
                ax.set_title(MODELS[model])
            else:
                ax.set_xticks(HORIZONS, [f"H{h}" for h in HORIZONS])
        axes[row_index, 0].set_ylabel(
            f"Change in {informed_role}\nrealized surplus fraction"
        )
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, ncol=2, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.01))
    fig.subplots_adjust(top=0.84, hspace=0.22, wspace=0.18)
    return fig


def market_strata(rows: list[dict]) -> plt.Figure:
    families = ("thin", "medium", "large")
    matrices = {}
    maximum = 0.0
    for model in MODELS:
        matrix = np.zeros((len(HORIZONS), len(families)))
        for row_index, horizon in enumerate(HORIZONS):
            for column, family in enumerate(families):
                matrix[row_index, column] = paired_interval(
                    rows, "exact", "coarse", "nash_score",
                    model=model, horizon=horizon, family=family,
                )[0]
        matrices[model] = matrix
        maximum = max(maximum, float(np.max(np.abs(matrix))))
    fig = plt.figure(figsize=(7.15, 2.25), layout="constrained")
    grid = fig.add_gridspec(1, len(MODELS) + 1, width_ratios=[1] * len(MODELS) + [0.07])
    axes = [fig.add_subplot(grid[0, column]) for column in range(len(MODELS))]
    colorbar_axis = fig.add_subplot(grid[0, len(MODELS)])
    image = None
    for ax, model in zip(axes, MODELS):
        matrix = matrices[model]
        image = ax.imshow(
            matrix, cmap=DELTA_CMAP, vmin=-maximum, vmax=maximum, aspect="auto"
        )
        ax.set_title(MODELS[model])
        ax.set_xticks(range(3), ["Thin", "Medium", "Large"], rotation=18, ha="right")
        ax.set_yticks(range(3), [f"H{h}" for h in HORIZONS])
        for row_index in range(3):
            for column in range(3):
                value = matrix[row_index, column]
                text_color = "white" if abs(value) > 0.55 * maximum else "#202124"
                ax.text(
                    column, row_index, f"{value:+.2f}", ha="center",
                    va="center", fontsize=7.0, color=text_color,
                )
    axes[0].set_ylabel("Message horizon")
    if image is not None:
        fig.colorbar(image, cax=colorbar_axis, label="Full minus coarse Nash score")
    return fig


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_tables(rows: list[dict], output: Path) -> None:
    episode_fields = [
        "model", "model_label", "horizon", "condition", "market_id",
        "base_market_id", "family", "first_mover", "agreement",
        "normalized_welfare", "potential_surplus", "accepted_price",
        "seller_share", "buyer_share", "seller_realized_fraction",
        "buyer_realized_fraction", "nash_score", "candidate_nash",
        "realization_loss", "turns_used", "proposal_count", "endpoint_offer",
    ]
    write_csv(output / "episode_metrics.csv", rows, episode_fields)

    summaries = []
    for model in MODELS:
        for horizon in HORIZONS:
            for condition in ALL_CONDITIONS:
                cell = select(rows, model=model, horizon=horizon, condition=condition)
                agreement = cluster_interval(cell, "agreement")
                nash = cluster_interval(cell, "nash_score")
                conditional_nash = cluster_interval(
                    [row for row in cell if row["agreement"]], "nash_score"
                )
                candidate = cluster_interval(cell, "candidate_nash")
                loss = cluster_interval(cell, "realization_loss")
                seller_share = cluster_interval(
                    [row for row in cell if row["seller_share"] is not None],
                    "seller_share",
                )
                summaries.append({
                    "model": MODELS[model],
                    "horizon": horizon,
                    "condition": CONDITION_LABELS[condition],
                    "episodes": len(cell),
                    "agreement_rate": agreement[0],
                    "agreement_ci_low": agreement[1],
                    "agreement_ci_high": agreement[2],
                    "normalized_welfare": safe_mean(row["normalized_welfare"] for row in cell),
                    "normalized_nash_product": nash[0],
                    "nash_ci_low": nash[1],
                    "nash_ci_high": nash[2],
                    "conditional_nash_product": conditional_nash[0],
                    "conditional_nash_ci_low": conditional_nash[1],
                    "conditional_nash_ci_high": conditional_nash[2],
                    "candidate_nash": candidate[0],
                    "candidate_nash_ci_low": candidate[1],
                    "candidate_nash_ci_high": candidate[2],
                    "realization_loss": loss[0],
                    "realization_loss_ci_low": loss[1],
                    "realization_loss_ci_high": loss[2],
                    "seller_share_given_agreement": seller_share[0],
                    "seller_share_ci_low": seller_share[1],
                    "seller_share_ci_high": seller_share[2],
                    "mean_turns": safe_mean(row["turns_used"] for row in cell),
                })
    write_csv(output / "condition_summary.csv", summaries, list(summaries[0]))

    contrasts = []
    for model in MODELS:
        for horizon in HORIZONS:
            for family in ("all", "thin", "medium", "large"):
                filters = {"model": model, "horizon": horizon}
                if family != "all":
                    filters["family"] = family
                agreement = paired_interval(rows, "exact", "coarse", "agreement", **filters)
                nash = paired_interval(rows, "exact", "coarse", "nash_score", **filters)
                candidate = paired_interval(rows, "exact", "coarse", "candidate_nash", **filters)
                loss = paired_interval(rows, "exact", "coarse", "realization_loss", **filters)
                contrasts.append({
                    "model": MODELS[model],
                    "horizon": horizon,
                    "family": family,
                    "full_minus_coarse_agreement": agreement[0],
                    "agreement_ci_low": agreement[1],
                    "agreement_ci_high": agreement[2],
                    "full_minus_coarse_nash": nash[0],
                    "nash_ci_low": nash[1],
                    "nash_ci_high": nash[2],
                    "full_minus_coarse_candidate_nash": candidate[0],
                    "candidate_nash_ci_low": candidate[1],
                    "candidate_nash_ci_high": candidate[2],
                    "full_minus_coarse_realization_loss": loss[0],
                    "realization_loss_ci_low": loss[1],
                    "realization_loss_ci_high": loss[2],
                })
    write_csv(output / "full_minus_coarse.csv", contrasts, list(contrasts[0]))


def write_readme(output: Path) -> None:
    (output / "README.md").write_text(
        f"""# Figure bargaining results

This directory is generated offline from the complete, snapshot-verified
profitable-market panel in `results/`.

## Evidence included

- {len(MODELS)} models: {', '.join(MODELS.values())}.
- Three horizons: H2, H4, H6.
- Thirty profitable mirrored markets per condition and horizon.
- Five conditions: least, coarse, full, seller informed, buyer informed.
- {30 * len(MODELS) * len(HORIZONS) * len(ALL_CONDITIONS):,} primary episodes in total. All {len(MODELS) * len(HORIZONS)} model-horizon audits pass and
  verify their frozen source snapshots.

## Metrics

`normalized_nash_product = 4 * seller_gain * buyer_gain / potential_surplus^2`
for an executed individually rational trade, and zero for disagreement. It is
one at an equal split. `candidate_nash` is the best Nash score among valid IR
prices proposed in the transcript. `realization_loss = candidate_nash -
normalized_nash_product`.

## Figures

1. `figure_1_information_horizon`: agreement and the normalized Nash product
   conditional on agreement for every model, horizon, and symmetric
   information condition.
2. `figure_2_nash_decomposition`: full-minus-coarse candidate gain, negative
   realization-loss contribution, and final Nash-product effect.
3. `figure_3_surplus_division`: accepted seller share, separated by starting
   role, model, horizon, and information condition.
4. `figure_4_asymmetric_placement`: change in the informed role's realized
   surplus fraction relative to coarse information, separated by whether that
   role moves first or second.
5. `figure_5_market_strata`: full-minus-coarse Nash-product effect in thin,
   medium, and large-surplus markets.

Every main-panel interval is a 95% cluster bootstrap over the fifteen base
cost--value pairs, keeping the two mirrored starting-role instances together.
The individual figures are provided as editable SVG and 300-dpi PNG files. The
multi-page `bargaining_figures.pdf` is a convenient figure atlas.
"""
    )


def generate(output: Path) -> None:
    set_style()
    output.mkdir(parents=True, exist_ok=True)
    rows = load_primary(RESULT_ROOT)
    validate_primary(rows)
    write_tables(rows, output)
    write_readme(output)
    with PdfPages(output / "bargaining_figures.pdf") as atlas:
        figures = [
            ("figure_1_information_horizon", information_and_nash(rows)),
            ("figure_2_nash_decomposition", nash_decomposition(rows)),
            ("figure_3_surplus_division", surplus_division(rows)),
            ("figure_4_asymmetric_placement", asymmetric_placement(rows)),
            ("figure_5_market_strata", market_strata(rows)),
        ]
        for stem, figure in figures:
            save_figure(figure, output, stem, atlas)
    print(f"Wrote figures and tables to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT_ROOT)
    generate(parser.parse_args().output)
