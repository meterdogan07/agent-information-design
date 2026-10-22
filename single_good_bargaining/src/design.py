"""All economic and experimental choices in one place."""

from __future__ import annotations

from experiment_runtime.models import model_catalog

import math
import random
from typing import Any


PROTOCOL_VERSION = "single_good_bargaining_v7_balanced_panel_20260824"
PROMPT_VERSION = "clear_game_final_action_v6_20260824"
PRIOR_ID = "independent_uniform_20_100_v1"
PANEL_ID = "five_per_ex_post_surplus_stratum_v1"
PROFITABLE_MIRRORED_PANEL_ID = "five_per_profitable_stratum_both_starters_v1"
PANEL_IDS = (PANEL_ID, PROFITABLE_MIRRORED_PANEL_ID)

HORIZONS = (2, 3, 4, 5, 6, 8)
MARKET_COUNT = 20
PROFITABLE_MIRRORED_MARKET_COUNT = 30
MARKET_SEED = 20260824
PRICE_MIN = 0.0
PRICE_MAX = 200.0
PRICE_EPSILON = 1e-9
MAX_PUBLIC_MESSAGE_CHARACTERS = 8_000
MAX_PRIVATE_REASONING_CHARACTERS = 8_000
MAX_PROMPT_CHARACTERS = 120_000

FAMILIES = ("no_trade", "thin", "medium", "large")
PROFITABLE_FAMILIES = ("thin", "medium", "large")

# Analytic probabilities under independent U[20,100] cost and value. These are
# used only to reweight the deliberately balanced evaluation panel back toward
# the population stated in the prompt.
FAMILY_POPULATION_WEIGHTS = {
    "no_trade": 0.5,
    "thin": 1087.5 / 6400.0,
    "medium": 1100.0 / 6400.0,
    "large": 1012.5 / 6400.0,
}
PROFITABLE_FAMILY_POPULATION_WEIGHTS = {
    family: FAMILY_POPULATION_WEIGHTS[family]
    / sum(FAMILY_POPULATION_WEIGHTS[name] for name in PROFITABLE_FAMILIES)
    for family in PROFITABLE_FAMILIES
}

# Each value is the information card shown to that role.  An informed role
# receives the identical exact card used in the both-exact condition; only the
# other role's card changes.  This keeps placement separate from wording.
CONDITION_SIGNALS = {
    "hidden": {"seller": "hidden", "buyer": "hidden"},
    "coarse": {"seller": "coarse", "buyer": "coarse"},
    "exact": {"seller": "exact", "buyer": "exact"},
    "seller_informed": {"seller": "exact", "buyer": "hidden"},
    "buyer_informed": {"seller": "hidden", "buyer": "exact"},
}
CONDITIONS = tuple(CONDITION_SIGNALS)
MAIN_CONDITIONS = ("hidden", "coarse", "exact")
CONDITION_SETS = {"all": CONDITIONS, "main": MAIN_CONDITIONS}

CONDITION_DESCRIPTIONS = {
    "hidden": "Neither agent sees the counterpart's reservation value.",
    "coarse": "Both learn only whether a mutually profitable price exists.",
    "exact": "Both see the seller cost and buyer value.",
    "seller_informed": "Only the seller sees the buyer's exact value.",
    "buyer_informed": "Only the buyer sees the seller's exact cost.",
}

# Current pinned model endpoints. The launcher uses the verified prices below
# so a paid run needs only one
# explicit API budget. Update the verification date and prices together.
MODEL_PRESETS = model_catalog("bargaining")


def classify_market(seller_cost: float, buyer_value: float) -> str:
    """Ex-post analysis label; this label is never shown to an agent."""
    gap = buyer_value - seller_cost
    if gap <= 0:
        return "no_trade"
    if gap <= 15:
        return "thin"
    if gap <= 35:
        return "medium"
    return "large"


def validate_market(market: dict[str, Any]) -> None:
    required = {
        "market_id", "base_market_id", "idx", "family", "seller_cost",
        "buyer_value", "first_mover", "prior_id", "panel_id",
    }
    if set(market) != required:
        raise ValueError("market fields differ from the documented schema")
    if market["first_mover"] not in {"seller", "buyer"}:
        raise ValueError("first mover must be seller or buyer")
    for name in ("seller_cost", "buyer_value"):
        value = market[name]
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"{name} must be a finite number")
        if not 20.0 <= float(value) <= 100.0:
            raise ValueError(f"{name} is outside the stated prior support")
    if market["family"] != classify_market(
        float(market["seller_cost"]), float(market["buyer_value"])
    ):
        raise ValueError("family is not the deterministic ex-post label")
    if market["prior_id"] != PRIOR_ID:
        raise ValueError("unexpected prior")
    if market["panel_id"] not in PANEL_IDS:
        raise ValueError("unexpected evaluation panel")
    if (
        market["panel_id"] == PROFITABLE_MIRRORED_PANEL_ID
        and market["family"] not in PROFITABLE_FAMILIES
    ):
        raise ValueError("mirrored profitable panel cannot contain no-trade markets")


def generate_markets(
    count: int = MARKET_COUNT,
    seed: int = MARKET_SEED,
    panel_id: str = PANEL_ID,
) -> list[dict[str, Any]]:
    """Generate either the preserved panel or the paired profitable panel."""
    if panel_id not in PANEL_IDS:
        raise ValueError(f"unknown panel: {panel_id}")
    if panel_id == PROFITABLE_MIRRORED_PANEL_ID:
        return generate_profitable_mirrored_markets(count, seed)
    if count <= 0 or count % len(FAMILIES) != 0:
        raise ValueError("market count must be a positive multiple of four")
    rng = random.Random(seed)
    markets = []
    per_family = count // len(FAMILIES)
    for family in FAMILIES:
        for _ in range(per_family):
            while True:
                # Draw from the same IID population stated in every prompt;
                # rejection sampling balances only the evaluation panel.
                seller_cost = round(rng.uniform(20.0, 100.0), 1)
                buyer_value = round(rng.uniform(20.0, 100.0), 1)
                if classify_market(seller_cost, buyer_value) == family:
                    break
            idx = len(markets)
            market = {
                "market_id": f"BARG7-{seed}-{idx:04d}",
                "base_market_id": f"BASE-{seed}-{idx:04d}",
                "idx": idx,
                "family": family,
                "seller_cost": seller_cost,
                "buyer_value": buyer_value,
                "first_mover": "seller" if idx % 2 == 0 else "buyer",
                "prior_id": PRIOR_ID,
                "panel_id": PANEL_ID,
            }
            validate_market(market)
            markets.append(market)
    return markets


def generate_profitable_mirrored_markets(
    count: int = PROFITABLE_MIRRORED_MARKET_COUNT,
    seed: int = MARKET_SEED,
) -> list[dict[str, Any]]:
    """Draw profitable base markets and run every pair under both starters.

    ``count`` is the number of market instances after mirroring. Therefore 30
    means five base cost-value pairs in each of three profitable families,
    with both seller-first and buyer-first versions of every base pair.
    """
    cells = len(PROFITABLE_FAMILIES) * 2
    if count <= 0 or count % cells != 0:
        raise ValueError("mirrored profitable market count must be a positive multiple of six")
    rng = random.Random(seed)
    markets: list[dict[str, Any]] = []
    bases_per_family = count // cells
    base_index = 0
    for family in PROFITABLE_FAMILIES:
        for _ in range(bases_per_family):
            while True:
                seller_cost = round(rng.uniform(20.0, 100.0), 1)
                buyer_value = round(rng.uniform(20.0, 100.0), 1)
                if classify_market(seller_cost, buyer_value) == family:
                    break
            base_market_id = f"BASE-MIRROR-{seed}-{base_index:04d}"
            for first_mover in ("seller", "buyer"):
                idx = len(markets)
                market = {
                    "market_id": f"BARG8-{seed}-{base_index:04d}-{first_mover}",
                    "base_market_id": base_market_id,
                    "idx": idx,
                    "family": family,
                    "seller_cost": seller_cost,
                    "buyer_value": buyer_value,
                    "first_mover": first_mover,
                    "prior_id": PRIOR_ID,
                    "panel_id": PROFITABLE_MIRRORED_PANEL_ID,
                }
                validate_market(market)
                markets.append(market)
            base_index += 1
    return markets


def signals_for(condition: str) -> dict[str, str]:
    if condition not in CONDITION_SIGNALS:
        raise ValueError(f"unknown condition: {condition}")
    return dict(CONDITION_SIGNALS[condition])
