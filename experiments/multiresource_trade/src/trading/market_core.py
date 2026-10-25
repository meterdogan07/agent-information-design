"""Generic market construction and researcher-side welfare calculations."""

from __future__ import annotations

import itertools
from typing import Any, Mapping

from .. import PROTOCOL_VERSION


RESOURCE_NAMES = ("X", "Y", "Z", "W", "U", "V", "P", "Q", "R", "S")
RESOURCES = RESOURCE_NAMES[:2]
ABUNDANT_QUANTITY = 5
SCARCE_QUANTITY = 1


def scale_responder_values(
    value_pair: tuple[int, int], resource_count: int
) -> tuple[int, int]:
    """Expand a low/high pair while preserving its economic proportions."""
    group_size = resource_count // 2
    low, high = value_pair
    return low * group_size, high * group_size


def make_market(
    a_type: str,
    b_type: str,
    orientation: str,
    abundant_quantity: int = ABUNDANT_QUANTITY,
    resource_count: int = 2,
    a_value_pair: tuple[int, int] | None = None,
    b_value_pair: tuple[int, int] | None = None,
    value_types: Mapping[str, tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Make one complementary-endowment market with an even resource count."""
    if orientation not in {"xy", "yx"}:
        raise ValueError("orientation must be 'xy' or 'yx'")
    if abundant_quantity <= SCARCE_QUANTITY:
        raise ValueError("abundant_quantity must exceed scarce_quantity")
    if resource_count not in {2, 4, 6}:
        raise ValueError("resource_count must be 2, 4, or 6")

    resources = RESOURCE_NAMES[:resource_count]
    midpoint = resource_count // 2
    first_group, second_group = resources[:midpoint], resources[midpoint:]
    abundant_a = first_group if orientation == "xy" else second_group
    abundant_b = second_group if orientation == "xy" else first_group
    if a_value_pair is None or b_value_pair is None:
        if value_types is None:
            raise ValueError("value_types are required when value pairs are omitted")
    low_a, high_a = a_value_pair or value_types[a_type]
    low_b, high_b = b_value_pair or value_types[b_type]

    def values(abundant: tuple[str, ...], low: int, high: int) -> dict[str, int]:
        scarce = tuple(item for item in resources if item not in abundant)
        result = {item: low + index for index, item in enumerate(abundant)}
        result.update({item: high - index for index, item in enumerate(scarce)})
        return {item: result[item] for item in resources}

    utility_a = values(abundant_a, low_a, high_a)
    utility_b = values(abundant_b, low_b, high_b)
    for actor, abundant, utility in (
        ("A", abundant_a, utility_a),
        ("B", abundant_b, utility_b),
    ):
        scarce = tuple(item for item in resources if item not in abundant)
        if max(utility[item] for item in abundant) >= min(
            utility[item] for item in scarce
        ):
            raise ValueError(
                f"{actor}'s value pair is too narrow for {resource_count} "
                "strictly ranked resources; use a wider-gap panel"
            )

    resource_tag = "" if resource_count == 2 else f"__resources{resource_count}"
    return {
        "design_version": PROTOCOL_VERSION,
        "market_id": (
            f"a-{a_type}__b-{b_type}__q{abundant_quantity}_1"
            f"{resource_tag}__{orientation}"
        ),
        "type_pair": f"a-{a_type}__b-{b_type}",
        "orientation": orientation,
        "resources": list(resources),
        "inventory_a": {
            item: abundant_quantity if item in abundant_a else SCARCE_QUANTITY
            for item in resources
        },
        "inventory_b": {
            item: abundant_quantity if item in abundant_b else SCARCE_QUANTITY
            for item in resources
        },
        "utility_a": utility_a,
        "utility_b": utility_b,
    }


def make_nested_market(
    panel_name: str,
    profile_name: str,
    pair_specs: tuple[tuple[int, int, int, int, int], ...],
    orientation: str,
    resource_count: int,
) -> dict[str, Any]:
    """Build a 2/4/6-resource market from nested complementary pairs."""
    if orientation not in {"xy", "yx"}:
        raise ValueError("orientation must be 'xy' or 'yx'")
    if resource_count < 2 or resource_count % 2 or resource_count > len(RESOURCE_NAMES):
        raise ValueError("resource_count must be even and between 2 and 10")
    if len(pair_specs) < resource_count // 2:
        raise ValueError("pair_specs do not cover the requested resources")

    resources = RESOURCE_NAMES[:resource_count]
    inventory_a: dict[str, int] = {}
    inventory_b: dict[str, int] = {}
    utility_a: dict[str, int] = {}
    utility_b: dict[str, int] = {}
    pair_quantities: dict[str, int] = {}

    for pair_index, (left, right) in enumerate(zip(resources[::2], resources[1::2])):
        quantity, a_owned, a_received, b_received, b_owned = pair_specs[pair_index]
        pair_quantities[f"{left}/{right}"] = quantity
        if orientation == "xy":
            inventory_a.update({left: quantity, right: SCARCE_QUANTITY})
            inventory_b.update({left: SCARCE_QUANTITY, right: quantity})
            utility_a.update({left: a_owned, right: a_received})
            utility_b.update({left: b_received, right: b_owned})
        else:
            inventory_a.update({left: SCARCE_QUANTITY, right: quantity})
            inventory_b.update({left: quantity, right: SCARCE_QUANTITY})
            utility_a.update({left: a_received, right: a_owned})
            utility_b.update({left: b_owned, right: b_received})

    if len(set(utility_a.values())) != resource_count or len(
        set(utility_b.values())
    ) != resource_count:
        raise ValueError("nested profiles must give each agent a strict ranking")

    id_prefix = (
        "heterogeneous"
        if panel_name == "heterogeneous_responder_extraction"
        else panel_name
    )
    return {
        "design_version": PROTOCOL_VERSION,
        "market_id": (
            f"{id_prefix}-{profile_name}__resources{resource_count}__{orientation}"
        ),
        "type_pair": f"{id_prefix}-{profile_name}",
        "orientation": orientation,
        "resources": list(resources),
        "inventory_a": inventory_a,
        "inventory_b": inventory_b,
        "utility_a": utility_a,
        "utility_b": utility_b,
        "pair_quantities": pair_quantities,
        "nested_resource_design": True,
    }


def gains(market: Mapping[str, Any], trade: Mapping[str, Any] | None) -> tuple[int, int]:
    """Return each agent's utility change from an accepted trade."""
    if trade is None:
        return 0, 0
    gain_a = sum(
        (trade["b_gives"].get(item, 0) - trade["a_gives"].get(item, 0))
        * market["utility_a"][item]
        for item in market["inventory_a"]
    )
    gain_b = sum(
        (trade["a_gives"].get(item, 0) - trade["b_gives"].get(item, 0))
        * market["utility_b"][item]
        for item in market["inventory_a"]
    )
    return gain_a, gain_b


def first_best_gain(market: Mapping[str, Any]) -> int:
    """Maximum welfare gain if each unit goes to its higher-valuing agent."""
    total = 0
    for item in market["inventory_a"]:
        quantity_a = market["inventory_a"][item]
        quantity_b = market["inventory_b"][item]
        value_a = market["utility_a"][item]
        value_b = market["utility_b"][item]
        initial = quantity_a * value_a + quantity_b * value_b
        best = (quantity_a + quantity_b) * max(value_a, value_b)
        total += best - initial
    return total


def full_exchange_trade(market: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """Move every abundant unit to the counterpart who values it more."""
    a_items = tuple(
        item
        for item in market["resources"]
        if market["inventory_a"][item] > market["inventory_b"][item]
    )
    b_items = tuple(
        item
        for item in market["resources"]
        if market["inventory_b"][item] > market["inventory_a"][item]
    )
    return {
        "a_gives": {item: market["inventory_a"][item] for item in a_items},
        "b_gives": {item: market["inventory_b"][item] for item in b_items},
    }


def welfare_maximizing_trade(
    market: Mapping[str, Any],
) -> dict[str, dict[str, int]]:
    """Allocate every unit to its higher-valuing agent."""
    trade = {"a_gives": {}, "b_gives": {}}
    for item in market["resources"]:
        value_a = market["utility_a"][item]
        value_b = market["utility_b"][item]
        if value_b > value_a:
            trade["a_gives"][item] = market["inventory_a"][item]
        elif value_a > value_b:
            trade["b_gives"][item] = market["inventory_b"][item]
    return trade


def best_strictly_ir_trade(
    market: Mapping[str, Any], extractor: str
) -> dict[str, Any]:
    """Find an extractor's best trade with positive gains for both agents.

    Dynamic programming keeps the best B gain at every attainable A gain.
    This avoids enumerating the full ten-resource Cartesian product while
    still considering every feasible integer net transfer.
    """
    if extractor not in {"A", "B"}:
        raise ValueError("extractor must be A or B")

    # A positive signed quantity moves from A to B; a negative quantity moves
    # from B to A.  The stored path has one signed quantity per resource.
    states: dict[int, tuple[int, tuple[int, ...]]] = {0: (0, ())}
    for item in market["resources"]:
        next_states: dict[int, tuple[int, tuple[int, ...]]] = {}
        for gain_a, (gain_b, path) in states.items():
            for transfer in range(
                -market["inventory_b"][item],
                market["inventory_a"][item] + 1,
            ):
                candidate_a = gain_a - transfer * market["utility_a"][item]
                candidate_b = gain_b + transfer * market["utility_b"][item]
                incumbent = next_states.get(candidate_a)
                if incumbent is None or candidate_b > incumbent[0]:
                    next_states[candidate_a] = (
                        candidate_b,
                        path + (transfer,),
                    )
        states = next_states

    candidates = [
        (gain_a, gain_b, path)
        for gain_a, (gain_b, path) in states.items()
        if gain_a > 0 and gain_b > 0
    ]
    if not candidates:
        raise ValueError("market has no strictly individually rational trade")
    if extractor == "A":
        gain_a, gain_b, path = max(
            candidates, key=lambda row: (row[0], row[1], row[0] + row[1])
        )
    else:
        gain_a, gain_b, path = max(
            candidates, key=lambda row: (row[1], row[0], row[0] + row[1])
        )

    trade = {"a_gives": {}, "b_gives": {}}
    for item, transfer in zip(market["resources"], path):
        if transfer > 0:
            trade["a_gives"][item] = transfer
        elif transfer < 0:
            trade["b_gives"][item] = -transfer

    first_best = first_best_gain(market)
    welfare = gain_a + gain_b
    return {
        "extractor": extractor,
        "gain_a": gain_a,
        "gain_b": gain_b,
        "extractor_gain": gain_a if extractor == "A" else gain_b,
        "counterpart_gain": gain_b if extractor == "A" else gain_a,
        "first_best_welfare": first_best,
        "extractive_welfare": welfare,
        "welfare_retention": welfare / first_best,
        "welfare_loss": 1.0 - welfare / first_best,
        "trade": trade,
    }


def extraction_opportunity(
    market: Mapping[str, Any], extractor: str
) -> dict[str, Any]:
    """Find an actor's highest-own-gain trade among strictly IR transfers."""
    if extractor not in {"A", "B"}:
        raise ValueError("extractor must be A or B")
    a_items = tuple(
        item
        for item in market["resources"]
        if market["inventory_a"][item] > market["inventory_b"][item]
    )
    b_items = tuple(
        item
        for item in market["resources"]
        if market["inventory_b"][item] > market["inventory_a"][item]
    )
    limits = tuple(market["inventory_a"][item] for item in a_items) + tuple(
        market["inventory_b"][item] for item in b_items
    )
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for quantities in itertools.product(*(range(limit + 1) for limit in limits)):
        trade = {
            "a_gives": {
                item: quantity
                for item, quantity in zip(a_items, quantities[: len(a_items)])
                if quantity
            },
            "b_gives": {
                item: quantity
                for item, quantity in zip(b_items, quantities[len(a_items) :])
                if quantity
            },
        }
        gain_a, gain_b = gains(market, trade)
        if gain_a > 0 and gain_b > 0:
            candidates.append((gain_b, gain_a, trade))
    if not candidates:
        raise ValueError("market has no strictly individually rational trade")

    if extractor == "B":
        gain_b, gain_a, trade = max(candidates, key=lambda row: (row[0], row[1]))
    else:
        gain_b, gain_a, trade = max(candidates, key=lambda row: (row[1], row[0]))
    first_best = first_best_gain(market)
    welfare = gain_a + gain_b
    full_gain_a, full_gain_b = gains(market, full_exchange_trade(market))
    extractor_gain = gain_a if extractor == "A" else gain_b
    counterpart_gain = gain_b if extractor == "A" else gain_a
    full_extractor_gain = full_gain_a if extractor == "A" else full_gain_b
    appropriation_advantage = extractor_gain - full_extractor_gain
    return {
        "extractor": extractor,
        "welfare_retention": welfare / first_best,
        "welfare_loss": 1.0 - welfare / first_best,
        "first_best_welfare": first_best,
        "extractive_welfare": welfare,
        "gain_a": gain_a,
        "gain_b": gain_b,
        "extractor_gain": extractor_gain,
        "counterpart_gain": counterpart_gain,
        "full_exchange_gain_a": full_gain_a,
        "full_exchange_gain_b": full_gain_b,
        "appropriation_advantage": appropriation_advantage,
        "normalized_appropriation_advantage": appropriation_advantage / first_best,
        "trade": trade,
    }


def responder_extraction_opportunity(market: Mapping[str, Any]) -> dict[str, Any]:
    """Backward-compatible subset of B's extraction diagnostics."""
    opportunity = extraction_opportunity(market, "B")
    fields = (
        "welfare_retention",
        "welfare_loss",
        "first_best_welfare",
        "extractive_welfare",
        "gain_a",
        "gain_b",
        "trade",
    )
    return {field: opportunity[field] for field in fields}
