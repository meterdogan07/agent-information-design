"""Natural double-auction markets, disclosures, and clearing.

This file is deterministic: it makes no model calls and writes no files.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import random
import re

from experiment_runtime import load_env_files, sha256_text

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(PACKAGE_DIR, "..", ".."))

PROTOCOL_VERSION = "double_auction_three_family_v1_20260903"
PIVOTAL_PROTOCOL_VERSION = "double_auction_pivotal_competition_v1_20260903"
SCALING_PROTOCOL_VERSION = "double_auction_uniform_size_scaling_v1_20260904"
OVERLAP_PROTOCOL_VERSION = "double_auction_distribution_overlap_v1_20260904"
MARGIN_PROTOCOL_VERSION = "double_auction_quantity_by_margin_v3_20260904"
MIRRORED_MARGIN_PROTOCOL_VERSION = "double_auction_role_mirror_v1_20260904"
EXPERIMENT_DIR_NAME = "market_phases"
DEFAULT_MARKETS = 30
DEFAULT_N_BUYERS = 3
DEFAULT_N_SELLERS = 3
TEMPERATURE_POLICY = "provider_default"
NATURAL_ENDPOINT_RANGE = (10.0, 90.0)

# These families form one ordered market-thickness treatment.  The number of
# efficient trades and the surplus of the marginal trade rise together.
MARKET_FAMILIES = {
    "thin": {"quantity": 1, "marginal_surplus": (2.0, 8.0), "level": "low"},
    "intermediate": {
        "quantity": 2,
        "marginal_surplus": (12.0, 24.0),
        "level": "middle",
    },
    "thick": {
        "quantity": 3,
        "marginal_surplus": (30.0, 50.0),
        "level": "high",
    },
}

# The overlap sweep keeps each support 60 points wide and moves the two
# populations toward one another.  A matched latent draw is reused at every
# level, so the only systematic difference is the distributional geometry.
OVERLAP_FAMILIES = {
    "low_overlap": {
        "buyer_support": (40.0, 100.0),
        "seller_support": (0.0, 60.0),
    },
    "medium_overlap": {
        "buyer_support": (30.0, 90.0),
        "seller_support": (10.0, 70.0),
    },
    "full_overlap": {
        "buyer_support": (20.0, 80.0),
        "seller_support": (20.0, 80.0),
    },
}

# The experiment crosses efficient quantity with the gains from the last
# efficient trade.  This makes the margin definition apply to any active q.
MARGIN_FAMILIES = {
    "narrow_margin": (2.0, 8.0),
    "medium_margin": (12.0, 24.0),
    "wide_margin": (35.0, 50.0),
}
MARGIN_QUANTITIES = (1, 2, 3)

MARKET_DESIGNS = (
    "three_family", "overlap_sweep", "margin_sweep", "margin_sweep_mirrored",
    "pivotal_competition", "size_scaling",
)


def protocol_version(market_design):
    """Return the immutable protocol label for one market design."""
    if market_design == "three_family":
        return PROTOCOL_VERSION
    if market_design == "pivotal_competition":
        return PIVOTAL_PROTOCOL_VERSION
    if market_design == "size_scaling":
        return SCALING_PROTOCOL_VERSION
    if market_design == "overlap_sweep":
        return OVERLAP_PROTOCOL_VERSION
    if market_design == "margin_sweep":
        return MARGIN_PROTOCOL_VERSION
    if market_design == "margin_sweep_mirrored":
        return MIRRORED_MARGIN_PROTOCOL_VERSION
    raise ValueError(f"unknown market design: {market_design}")

FEEDBACK_POLICIES = ("none", "private_status")

CONDITION_SETS = {
    "endpoint_precision": (
        "least_information",
        "coarse_information",
        "full_information",
    ),
    "endpoint_placement": (
        "coarse_information",
        "buyers_full_information",
        "sellers_full_information",
        "full_information",
    ),
    "least_full": (
        "least_information",
        "full_information",
    ),
}

CONTRASTS_BY_CONDITION_SET = {
    "endpoint_precision": (
        ("coarse_minus_least", "coarse_information", "least_information"),
        ("full_minus_coarse", "full_information", "coarse_information"),
        ("full_minus_least", "full_information", "least_information"),
    ),
    "endpoint_placement": (
        ("buyers_full_minus_coarse",
         "buyers_full_information", "coarse_information"),
        ("sellers_full_minus_coarse",
         "sellers_full_information", "coarse_information"),
        ("both_full_minus_coarse",
         "full_information", "coarse_information"),
    ),
    "least_full": (
        ("full_minus_least", "full_information", "least_information"),
    ),
}


def conditions_for(name):
    try:
        return CONDITION_SETS[name]
    except KeyError as error:
        raise ValueError(f"unknown condition set {name!r}") from error


# ---------------------------------------------------------------------------
# Reproducibility helpers
# ---------------------------------------------------------------------------

def load_env():
    """Load the first repository .env file without overwriting shell values."""
    load_env_files((
        Path(PROJECT_ROOT).parents[1] / ".env",
        Path(PROJECT_ROOT) / ".env",
    ))


def model_slug(model):
    return model.replace("/", "_").replace(":", "_")


# ---------------------------------------------------------------------------
# Natural market panel
# ---------------------------------------------------------------------------

def _random_stream(*parts):
    """Return a stable random stream without coupling different market draws."""
    payload = ":".join(map(str, parts))
    seed = int(sha256_text(payload)[:16], 16)
    return random.Random(seed)

def first_best(values, costs):
    """Match highest values to lowest costs while gains from trade are positive."""
    buyers = sorted(values.items(), key=lambda item: item[1], reverse=True)
    sellers = sorted(costs.items(), key=lambda item: item[1])
    trades, welfare = [], 0.0
    for (buyer, value), (seller, cost) in zip(buyers, sellers):
        if value <= cost:
            break
        surplus = value - cost
        trades.append({"buyer": buyer, "seller": seller, "surplus": surplus})
        welfare += surplus
    return {"welfare": welfare, "trades": trades}


def _candidate(seed, attempt, size=3, stream="natural-candidate"):
    """Draw one iid 3x3 market from the common endpoint population."""
    rng = _random_stream(seed, stream, attempt)
    values = {
        f"B{number}": round(rng.uniform(*NATURAL_ENDPOINT_RANGE), 1)
        for number in range(1, size + 1)
    }
    costs = {
        f"S{number}": round(rng.uniform(*NATURAL_ENDPOINT_RANGE), 1)
        for number in range(1, size + 1)
    }
    return values, costs


def _family_cores(n, seed):
    """Select equal thin, intermediate, and thick natural markets."""
    family_names = tuple(MARKET_FAMILIES)
    desired_families = [family_names[index % len(family_names)] for index in range(n)]
    needed = {name: desired_families.count(name) for name in family_names}
    pool_target = max(100, 20 * max(needed.values(), default=0))
    pools = {name: [] for name in family_names}

    attempt = 0
    while any(len(pool) < pool_target for pool in pools.values()):
        values, costs = _candidate(seed, attempt)
        optimum = first_best(values, costs)
        quantity = len(optimum["trades"])
        marginal = optimum["trades"][-1]["surplus"] if quantity else None
        for name, specification in MARKET_FAMILIES.items():
            low, high = specification["marginal_surplus"]
            if (quantity == specification["quantity"]
                    and low <= marginal <= high
                    and len(pools[name]) < pool_target):
                pools[name].append({
                    "values": values,
                    "costs": costs,
                    "marginal_surplus": marginal,
                })
                break
        attempt += 1
        if attempt > 1_000_000:
            raise RuntimeError("could not generate the three-family market panel")

    selected = {}
    for name, count in needed.items():
        ordered = sorted(pools[name], key=lambda row: row["marginal_surplus"])
        selected[name] = []
        for rank in range(count):
            target_quantile = (rank + 0.5) / count
            source = ordered[min(
                len(ordered) - 1,
                int(target_quantile * len(ordered)),
            )]
            selected[name].append({
                **source,
                "target_marginal_surplus_quantile": target_quantile,
            })

    used = {name: 0 for name in family_names}
    cores = []
    for index, name in enumerate(desired_families):
        specification = MARKET_FAMILIES[name]
        core = selected[name][used[name]]
        used[name] += 1
        cores.append((
            index,
            name,
            specification["quantity"],
            core["values"],
            core["costs"],
            core["target_marginal_surplus_quantile"],
            specification["level"],
        ))
    return cores


def _extra_endpoint(seed, market_index, role, agent):
    rng = _random_stream(seed, "natural-extra", market_index, role, agent)
    return round(rng.uniform(*NATURAL_ENDPOINT_RANGE), 1)


def _overlap_instances(n, n_buyers, n_sellers, seed):
    """Generate three matched buyer--seller support-overlap levels."""
    if n % len(OVERLAP_FAMILIES):
        raise ValueError("overlap sweep market count must be divisible by 3")

    result = []
    base_draws = n // len(OVERLAP_FAMILIES)
    accepted, attempt = 0, 0
    while accepted < base_draws:
        rng = _random_stream(seed, "overlap-sweep", attempt)
        buyer_positions = [rng.random() for _ in range(n_buyers)]
        seller_positions = [rng.random() for _ in range(n_sellers)]

        matched = []
        for family, specification in OVERLAP_FAMILIES.items():
            buyer_low, buyer_high = specification["buyer_support"]
            seller_low, seller_high = specification["seller_support"]
            values = {
                f"B{i + 1}": round(buyer_low + (buyer_high - buyer_low) * position, 1)
                for i, position in enumerate(buyer_positions)
            }
            costs = {
                f"S{i + 1}": round(seller_low + (seller_high - seller_low) * position, 1)
                for i, position in enumerate(seller_positions)
            }
            optimum = first_best(values, costs)
            matched.append((family, specification, values, costs, optimum))

        # Welfare normalization is undefined when no efficient trade exists.
        # Reject only such degenerate matched draws, at every overlap level.
        if all(row[-1]["welfare"] > 0 for row in matched):
            for family, specification, values, costs, optimum in matched:
                buyer_support = specification["buyer_support"]
                seller_support = specification["seller_support"]
                quantity = len(optimum["trades"])
                overlap = max(
                    0.0,
                    min(buyer_support[1], seller_support[1])
                    - max(buyer_support[0], seller_support[0]),
                )
                result.append({
                    "idx": len(result),
                    "regime": family,
                    "market_family": family,
                    "market_design": "overlap_sweep",
                    "matched_draw_index": accepted,
                    "buyer_support": list(buyer_support),
                    "seller_support": list(seller_support),
                    "support_overlap": overlap,
                    "core_efficient_quantity": quantity,
                    "surplus_level": "distribution_sweep",
                    "target_marginal_surplus_quantile": None,
                    "buyers": list(values),
                    "sellers": list(costs),
                    "values": values,
                    "costs": costs,
                    "first_best_welfare": optimum["welfare"],
                    "first_best_trades": optimum["trades"],
                    "efficient_quantity": quantity,
                    "mean_first_best_surplus": optimum["welfare"] / quantity,
                    "marginal_first_best_surplus": optimum["trades"][-1]["surplus"],
                })
            accepted += 1
        attempt += 1
        if attempt > 1_000_000:
            raise RuntimeError("could not generate overlap-sweep markets")
    return result


def _margin_instances(n, seed):
    """Cross q=1,2,3 with three marginal-surplus ranges in natural markets."""
    cells = [
        (family, quantity)
        for family in MARGIN_FAMILIES
        for quantity in MARGIN_QUANTITIES
    ]
    if n % 3:
        raise ValueError("margin sweep market count must be divisible by 3")

    # Start with equal cell counts. For panels such as n=30, rotate the three
    # remaining observations across the diagonal. This leaves every margin
    # family and every efficient quantity represented exactly n/3 times.
    per_cell, remainder = divmod(n, len(cells))
    needed = {cell: per_cell for cell in cells}
    if remainder == 3:
        for index in range(3):
            needed[(tuple(MARGIN_FAMILIES)[index], MARGIN_QUANTITIES[index])] += 1
    elif remainder == 6:
        for family_index, family in enumerate(MARGIN_FAMILIES):
            for quantity_index, quantity in enumerate(MARGIN_QUANTITIES):
                if family_index != quantity_index:
                    needed[(family, quantity)] += 1

    desired = [
        cell
        for repeat in range(max(needed.values()))
        for cell in cells
        if repeat < needed[cell]
    ]
    pool_target = max(30, 8 * max(needed.values()))
    pools = {cell: [] for cell in cells}

    attempt = 0
    while any(len(pool) < pool_target for pool in pools.values()):
        rng = _random_stream(seed, "margin-sweep", attempt)
        values = {f"B{i}": round(rng.uniform(0, 100), 1) for i in range(1, 4)}
        costs = {f"S{i}": round(rng.uniform(0, 100), 1) for i in range(1, 4)}
        sorted_values = sorted(values.values(), reverse=True)
        sorted_costs = sorted(costs.values())
        gaps = [value - cost for value, cost in zip(sorted_values, sorted_costs)]

        quantity = sum(gap > 0 for gap in gaps)
        if quantity:
            marginal_surplus = gaps[quantity - 1]
            for family, (low, high) in MARGIN_FAMILIES.items():
                cell = (family, quantity)
                if (low <= marginal_surplus <= high
                        and len(pools[cell]) < pool_target):
                    pools[cell].append({
                        "values": values,
                        "costs": costs,
                        "marginal_surplus": marginal_surplus,
                    })
                    break
        attempt += 1
        if attempt > 1_000_000:
            raise RuntimeError("could not generate fixed-quantity margin markets")

    selected = {}
    for cell, count in needed.items():
        ordered = sorted(pools[cell], key=lambda row: row["marginal_surplus"])
        selected[cell] = []
        for rank in range(count):
            quantile = (rank + 0.5) / count
            source = ordered[min(len(ordered) - 1, int(quantile * len(ordered)))]
            selected[cell].append((source, quantile))

    used, result = {cell: 0 for cell in cells}, []
    for family, quantity in desired:
        cell = (family, quantity)
        source, quantile = selected[cell][used[cell]]
        used[cell] += 1
        values, costs = source["values"], source["costs"]
        optimum = first_best(values, costs)
        result.append({
            "idx": len(result),
            "regime": f"{family}_q{quantity}",
            "market_family": family,
            "market_design": "margin_sweep",
            "core_efficient_quantity": quantity,
            "surplus_level": family.removesuffix("_margin"),
            "target_marginal_surplus_quantile": quantile,
            "buyers": list(values),
            "sellers": list(costs),
            "values": values,
            "costs": costs,
            "first_best_welfare": optimum["welfare"],
            "first_best_trades": optimum["trades"],
            "efficient_quantity": len(optimum["trades"]),
            "mean_first_best_surplus": optimum["welfare"] / quantity,
            "marginal_first_best_surplus": optimum["trades"][-1]["surplus"],
        })
    return result


def _mirror_margin_instance(row):
    """Swap buyer and seller positions without changing gains from trade.

    With endpoints on [0, 100], an original seller cost c becomes a mirrored
    buyer value 100-c, and an original buyer value v becomes a mirrored seller
    cost 100-v. Under the price map p' = 100-p, every feasible trade preserves
    both participants' payoff and total surplus.
    """
    values = {
        f"B{i}": round(100.0 - row["costs"][f"S{i}"], 1)
        for i in range(1, 4)
    }
    costs = {
        f"S{i}": round(100.0 - row["values"][f"B{i}"], 1)
        for i in range(1, 4)
    }
    optimum = first_best(values, costs)
    quantity = len(optimum["trades"])
    if quantity != row["efficient_quantity"]:
        raise AssertionError("role mirroring changed efficient quantity")
    if not math.isclose(optimum["welfare"], row["first_best_welfare"]):
        raise AssertionError("role mirroring changed first-best welfare")

    mirrored = dict(row)
    mirrored.update({
        "regime": f"mirrored_{row['regime']}",
        "market_design": "margin_sweep_mirrored",
        "buyers": list(values),
        "sellers": list(costs),
        "values": values,
        "costs": costs,
        "first_best_welfare": optimum["welfare"],
        "first_best_trades": optimum["trades"],
        "efficient_quantity": quantity,
        "mean_first_best_surplus": optimum["welfare"] / quantity,
        "marginal_first_best_surplus": optimum["trades"][-1]["surplus"],
    })
    return mirrored


def _scaling_instances(n, n_buyers, n_sellers, seed):
    """Return nested iid markets that remain active at 3x3, 6x6, and 10x10.

    The same market index contains the same first agents at every roster size.
    Selection only excludes degenerate panels in which everyone or no one
    should trade at one of the three planned sizes.
    """
    if n_buyers != n_sellers or n_buyers not in {3, 6, 10}:
        raise ValueError("size scaling supports matched 3x3, 6x6, or 10x10 rosters")

    selected, attempt = [], 0
    while len(selected) < n:
        values, costs = _candidate(
            seed, attempt, size=10, stream="uniform-size-scaling",
        )
        valid = True
        for size in (3, 6, 10):
            small_values = {f"B{i}": values[f"B{i}"] for i in range(1, size + 1)}
            small_costs = {f"S{i}": costs[f"S{i}"] for i in range(1, size + 1)}
            quantity = len(first_best(small_values, small_costs)["trades"])
            if not 0 < quantity < size:
                valid = False
                break
        if valid:
            selected.append((values, costs))
        attempt += 1
        if attempt > 1_000_000:
            raise RuntimeError("could not generate nested size-scaling markets")

    result = []
    for index, (all_values, all_costs) in enumerate(selected):
        values = {f"B{i}": all_values[f"B{i}"] for i in range(1, n_buyers + 1)}
        costs = {f"S{i}": all_costs[f"S{i}"] for i in range(1, n_sellers + 1)}
        optimum = first_best(values, costs)
        quantity = len(optimum["trades"])
        result.append({
            "idx": index,
            "regime": "size_scaling",
            "market_family": "size_scaling",
            "market_design": "size_scaling",
            "mechanism": "uniform_price",
            "buyers": list(values),
            "sellers": list(costs),
            "values": values,
            "costs": costs,
            "first_best_welfare": optimum["welfare"],
            "first_best_trades": optimum["trades"],
            "efficient_quantity": quantity,
            "mean_first_best_surplus": optimum["welfare"] / quantity,
            "marginal_first_best_surplus": optimum["trades"][-1]["surplus"],
            "core_efficient_quantity": len(first_best(
                {key: all_values[key] for key in list(all_values)[:3]},
                {key: all_costs[key] for key in list(all_costs)[:3]},
            )["trades"]),
            "surplus_level": "iid",
            "target_marginal_surplus_quantile": None,
        })
    return result


def _pivotal_instances(n, seed):
    """Generate 6x6 markets with a consequential marginal third trade.

    Endpoints remain iid Uniform[10, 90] candidates.  Rejection sampling keeps
    markets with two robust efficient trades, a narrow third trade, and a
    nearly profitable fourth pair.  Thus not every participant should trade.
    """
    pool = []
    attempt = 0
    pool_target = max(100, 20 * n)
    while len(pool) < pool_target:
        values, costs = _candidate(
            seed, attempt, size=6, stream="pivotal-candidate",
        )
        sorted_values = sorted(values.values(), reverse=True)
        sorted_costs = sorted(costs.values())
        gaps = [value - cost for value, cost in zip(sorted_values, sorted_costs)]
        if (gaps[0] >= 30 and gaps[1] >= 20
                and 4 <= gaps[2] <= 12 and -12 <= gaps[3] <= 0):
            pool.append({"values": values, "costs": costs, "gaps": gaps})
        attempt += 1
        if attempt > 1_000_000:
            raise RuntimeError("could not generate pivotal-competition markets")

    ordered = sorted(pool, key=lambda row: row["gaps"][2])
    result = []
    for index in range(n):
        quantile = (index + 0.5) / n
        source = ordered[min(len(ordered) - 1, int(quantile * len(ordered)))]
        values, costs = source["values"], source["costs"]
        optimum = first_best(values, costs)
        result.append({
            "idx": index,
            "regime": "pivotal_competition",
            "market_family": "pivotal_competition",
            "market_design": "pivotal_competition",
            "core_efficient_quantity": 3,
            "surplus_level": "mixed",
            "target_marginal_surplus_quantile": quantile,
            "buyers": list(values),
            "sellers": list(costs),
            "values": values,
            "costs": costs,
            "first_best_welfare": optimum["welfare"],
            "first_best_trades": optimum["trades"],
            "efficient_quantity": len(optimum["trades"]),
            "mean_first_best_surplus": optimum["welfare"] / 3,
            "marginal_first_best_surplus": optimum["trades"][-1]["surplus"],
        })
    return result


def gen_instances(n, n_buyers=3, n_sellers=3, seed=20260817,
                  market_design="three_family"):
    """Generate matched natural panels for every supported roster size."""
    if n <= 0 or n_buyers < 3 or n_sellers < 3:
        raise ValueError("natural panels need n>0 and at least 3 agents per side")
    if market_design == "pivotal_competition":
        if (n_buyers, n_sellers) != (6, 6):
            raise ValueError("pivotal competition requires six agents per side")
        return _pivotal_instances(n, seed)
    if market_design == "size_scaling":
        return _scaling_instances(n, n_buyers, n_sellers, seed)
    if market_design == "overlap_sweep":
        return _overlap_instances(n, n_buyers, n_sellers, seed)
    if market_design in {"margin_sweep", "margin_sweep_mirrored"}:
        if (n_buyers, n_sellers) != (3, 3):
            raise ValueError("margin sweep requires three agents per side")
        rows = _margin_instances(n, seed)
        if market_design == "margin_sweep_mirrored":
            return [_mirror_margin_instance(row) for row in rows]
        return rows
    if market_design != "three_family":
        raise ValueError(f"unknown market design: {market_design}")

    result = []
    for (
        index,
        family,
        core_quantity,
        core_values,
        core_costs,
        target_quantile,
        surplus_level,
    ) in _family_cores(n, seed):
        values, costs = dict(core_values), dict(core_costs)
        for number in range(4, n_buyers + 1):
            agent = f"B{number}"
            values[agent] = _extra_endpoint(seed, index, "buyer", agent)
        for number in range(4, n_sellers + 1):
            agent = f"S{number}"
            costs[agent] = _extra_endpoint(seed, index, "seller", agent)

        optimum = first_best(values, costs)
        quantity = len(optimum["trades"])
        result.append({
            "idx": index,
            "regime": family if (n_buyers, n_sellers) == (3, 3) else f"{family}_core",
            "market_family": family,
            "market_design": "three_family",
            "core_efficient_quantity": core_quantity,
            "surplus_level": surplus_level,
            "target_marginal_surplus_quantile": target_quantile,
            "buyers": list(values),
            "sellers": list(costs),
            "values": values,
            "costs": costs,
            "first_best_welfare": optimum["welfare"],
            "first_best_trades": optimum["trades"],
            "efficient_quantity": quantity,
            "mean_first_best_surplus": optimum["welfare"] / quantity,
            "marginal_first_best_surplus": optimum["trades"][-1]["surplus"],
        })
    return result


def parse_number(text, key):
    try:
        match = re.search(r"\{[\s\S]*\}", text)
        value = float(json.loads(match.group(0))[key]) if match else None
        return value if value is not None and math.isfinite(value) else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Order validation and midpoint clearing
# ---------------------------------------------------------------------------

def validate_orders(values, costs, submitted_bids, submitted_asks):
    bids, asks, invalid = {}, {}, []
    for buyer, value in values.items():
        bid = submitted_bids.get(buyer)
        if bid is None or not math.isfinite(bid) or not 0 <= bid <= value:
            bids[buyer] = None
            if bid is not None:
                invalid.append({"who": buyer, "role": "buyer", "order": bid,
                                "endpoint": value, "reason": "bid_outside_range"})
        else:
            bids[buyer] = bid
    for seller, cost in costs.items():
        ask = submitted_asks.get(seller)
        if ask is None or not math.isfinite(ask) or ask < cost:
            asks[seller] = None
            if ask is not None:
                invalid.append({"who": seller, "role": "seller", "order": ask,
                                "endpoint": cost, "reason": "ask_outside_range"})
        else:
            asks[seller] = ask
    return bids, asks, invalid


def clear_market(values, costs, bids, asks, mechanism="midpoint"):
    buyers = sorted(
        ((buyer, bids[buyer]) for buyer in values if bids.get(buyer) is not None),
        key=lambda item: (-item[1], item[0]),
    )
    sellers = sorted(
        ((seller, asks[seller]) for seller in costs if asks.get(seller) is not None),
        key=lambda item: (item[1], item[0]),
    )
    if mechanism == "uniform_price":
        quantity = 0
        for (_, bid), (_, ask) in zip(buyers, sellers):
            if bid < ask:
                break
            quantity += 1
        if not quantity:
            return {"trades": [], "welfare": 0.0,
                    "buyer_surplus": 0.0, "seller_surplus": 0.0}
        price = (buyers[quantity - 1][1] + sellers[quantity - 1][1]) / 2.0
        trades = [
            {
                "buyer": buyers[index][0], "seller": sellers[index][0],
                "bid": buyers[index][1], "ask": sellers[index][1],
                "price": price, "buyer_price": price, "seller_price": price,
            }
            for index in range(quantity)
        ]
        return {
            "trades": trades,
            "welfare": sum(values[t["buyer"]] - costs[t["seller"]] for t in trades),
            "buyer_surplus": sum(values[t["buyer"]] - price for t in trades),
            "seller_surplus": sum(price - costs[t["seller"]] for t in trades),
        }
    if mechanism != "midpoint":
        raise ValueError(f"unknown clearing mechanism: {mechanism}")

    trades, used_sellers = [], set()
    for buyer, bid in buyers:
        for seller, ask in sellers:
            if seller in used_sellers or bid < ask:
                continue
            price = (bid + ask) / 2.0
            trades.append({
                "buyer": buyer, "seller": seller,
                "bid": bid, "ask": ask, "price": price,
                "buyer_price": price, "seller_price": price,
            })
            used_sellers.add(seller)
            break

    return {
        "trades": trades,
        "welfare": sum(values[t["buyer"]] - costs[t["seller"]] for t in trades),
        "buyer_surplus": sum(values[t["buyer"]] - t["price"] for t in trades),
        "seller_surplus": sum(t["price"] - costs[t["seller"]] for t in trades),
    }
