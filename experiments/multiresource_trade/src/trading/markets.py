"""Select and generate named market panels.

Numerical inputs live in ``market_profiles.py``. Generic market construction
and welfare mathematics live in ``market_core.py``.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

from .market_core import (
    ABUNDANT_QUANTITY,
    RESOURCES,
    best_strictly_ir_trade,
    extraction_opportunity,
    first_best_gain,
    full_exchange_trade,
    gains,
    make_market as _make_market,
    make_nested_market,
    responder_extraction_opportunity,
    scale_responder_values,
    welfare_maximizing_trade,
)
from .market_profiles import (
    CONSTANT_EXTRACTION_PROFILES,
    CONSTANT_EXTRACTION_TARGET,
    CONSTANT_EXTRACTION_TOLERANCE,
    EXTRACTION_REGION_VALUE_PAIRS,
    EXTRACTION_ROLES,
    HETEROGENEOUS_COMPLEXITY_PAIR_ROLES,
    HETEROGENEOUS_COMPLEXITY_PROFILES,
    PROPOSER_EXTRACTION_TYPE_PAIRS,
    RESPONDER_EXTRACTION_CROSSED_A_VALUES,
    RESPONDER_EXTRACTION_CROSSED_B_VALUES,
    RESPONDER_EXTRACTION_HOLDOUT_PROFILES,
    RESPONDER_EXTRACTION_PROFILES,
    SYMMETRIC_EXTRACTION_PROFILES,
    VALUE_TYPES,
)

# ---------------------------------------------------------------------------
# Panel catalog: use this to choose an experiment before reading the profiles.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketPanel:
    """Everything the runners need to know about one named panel."""

    label: str
    group: str
    data_group: str
    varied_quantity: bool = False
    requires_first_mover_a: bool = False
    record_value_expansion: bool = False


# One catalog replaces separate label, grouping, quantity, role, and path sets.
# Existing names remain stable because result artifacts store them.
MARKET_CATALOG: dict[str, MarketPanel] = {
    "responder_extraction_crossed": MarketPanel(
        "Focused 30-market responder panel",
        "focused_experiment",
        "current",
        varied_quantity=True,
        requires_first_mover_a=True,
        record_value_expansion=True,
    ),
    "responder_extraction_holdout": MarketPanel(
        "Responder-extraction holdout",
        "validation",
        "ablations/roles",
        requires_first_mover_a=True,
        record_value_expansion=True,
    ),
    "responder_extraction": MarketPanel(
        "Responder-extraction role ablation",
        "role_ablations",
        "ablations/roles",
        requires_first_mover_a=True,
        record_value_expansion=True,
    ),
    "proposer_extraction": MarketPanel(
        "Proposer-extraction role ablation",
        "role_ablations",
        "ablations/roles",
        requires_first_mover_a=True,
    ),
    "symmetric_extraction": MarketPanel(
        "Equal-extraction role ablation",
        "role_ablations",
        "ablations/roles",
    ),
    "constant_extraction_scaling": MarketPanel(
        "Constant-severity resource scaling",
        "resource_count_ablation",
        "ablations/resource_count",
        varied_quantity=True,
        requires_first_mover_a=True,
    ),
    "heterogeneous_complexity_scaling": MarketPanel(
        "Nested resource-complexity study",
        "resource_count_ablation",
        "ablations/resource_count",
        varied_quantity=True,
        requires_first_mover_a=True,
    ),
    "extraction_region_grid": MarketPanel(
        "Main 90-market role, quantity, and extraction-pressure grid",
        "geometry_grid",
        "ablations/region_grid",
        varied_quantity=True,
        requires_first_mover_a=True,
    ),
    "calibration_holdout60_v1": MarketPanel(
        "Frozen 60-market prospective disclosure calibration holdout",
        "calibration",
        "calibration",
        varied_quantity=True,
        requires_first_mover_a=True,
    ),
}

MARKET_PANELS = tuple(MARKET_CATALOG)


def market_panel(name: str) -> MarketPanel:
    """Return the single authoritative definition for a panel name."""
    try:
        return MARKET_CATALOG[name]
    except KeyError as error:
        raise ValueError(f"unknown market panel: {name}") from error

# ---------------------------------------------------------------------------
# Shared builder
# ---------------------------------------------------------------------------


def make_market(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Build a market using the repository's frozen default value types."""
    kwargs.setdefault("value_types", VALUE_TYPES)
    return _make_market(*args, **kwargs)


# ---------------------------------------------------------------------------
# Main experiment: 30 crossed responder-extraction markets
# ---------------------------------------------------------------------------


def _generate_responder_extraction_crossed(
    resource_count: int,
) -> list[dict[str, Any]]:
    """Generate the main panel: 5 quantities x 2 A types x 3 B types.

    A proposes first and has steep preferences. B responds and has flatter
    preferences, so exact information can help B demand more of A's valuable
    resource. Orientations alternate to avoid tying a role to a resource name.
    """
    markets = []
    for quantity_index, quantity in enumerate(range(2, 7)):
        for a_index, (a_label, a_values) in enumerate(
            RESPONDER_EXTRACTION_CROSSED_A_VALUES
        ):
            for b_index, (b_label, b_values) in enumerate(
                RESPONDER_EXTRACTION_CROSSED_B_VALUES
            ):
                orientation = (
                    "xy"
                    if (quantity_index + a_index + b_index) % 2 == 0
                    else "yx"
                )
                markets.append(
                    make_market(
                        a_label,
                        b_label,
                        orientation,
                        quantity,
                        resource_count,
                        scale_responder_values(a_values, resource_count),
                        scale_responder_values(b_values, resource_count),
                    )
                )
    return markets


# ---------------------------------------------------------------------------
# Role ablations: who has the opportunity to extract surplus?
# ---------------------------------------------------------------------------


def _generate_responder_extraction(
    panel: str,
    abundant_quantity: int,
    resource_count: int,
) -> list[dict[str, Any]]:
    """Generate the original or holdout responder-extraction profiles."""
    profiles = (
        RESPONDER_EXTRACTION_HOLDOUT_PROFILES
        if panel == "responder_extraction_holdout"
        else RESPONDER_EXTRACTION_PROFILES
    )
    return [
        make_market(
            a_label,
            b_label,
            orientation,
            abundant_quantity,
            resource_count,
            scale_responder_values(a_values, resource_count),
            scale_responder_values(b_values, resource_count),
        )
        for (a_label, b_label, a_values, b_values), orientation in itertools.product(
            profiles,
            ("xy", "yx"),
        )
    ]


def _generate_proposer_extraction(
    abundant_quantity: int,
    resource_count: int,
) -> list[dict[str, Any]]:
    """Generate markets where first-moving A has the steeper value gap."""
    return [
        make_market(a_type, b_type, orientation, abundant_quantity, resource_count)
        for (a_type, b_type), orientation in itertools.product(
            PROPOSER_EXTRACTION_TYPE_PAIRS,
            ("xy", "yx"),
        )
    ]


def _generate_symmetric_extraction(
    abundant_quantity: int,
    resource_count: int,
) -> list[dict[str, Any]]:
    """Generate the equal-opportunity control: A and B have the same value gap."""
    markets = [
        make_market(
            value_label,
            value_label,
            orientation,
            abundant_quantity,
            resource_count,
            scale_responder_values(value_pair, resource_count),
            scale_responder_values(value_pair, resource_count),
        )
        for (value_label, value_pair), orientation in itertools.product(
            SYMMETRIC_EXTRACTION_PROFILES,
            ("xy", "yx"),
        )
    ]
    for market in markets:
        market["market_panel"] = "symmetric_extraction"
        market["extraction_symmetry"] = "matched_value_gap"
        if resource_count > 2:
            market["value_expansion_scale"] = resource_count // 2
    return markets


# ---------------------------------------------------------------------------
# Resource-count ablation: preserve extraction severity as size increases
# ---------------------------------------------------------------------------


def _generate_constant_extraction_scaling(
    resource_count: int,
) -> list[dict[str, Any]]:
    """Generate 2/4/6-resource markets with approximately fixed temptation."""
    return [
        make_nested_market(
            "constant_extraction_scaling",
            profile_name,
            pair_specs,
            orientation,
            resource_count,
        )
        for (profile_name, pair_specs), orientation in itertools.product(
            CONSTANT_EXTRACTION_PROFILES,
            ("xy", "yx"),
        )
    ]


def _generate_heterogeneous_complexity_scaling(
    resource_count: int,
) -> list[dict[str, Any]]:
    """Generate 12 nested markets at 2, 6, or 10 resources.

    The first pair is the responder-extraction core.  At six resources two
    offsetting compensation pairs make acceptance depend on the whole bundle.
    At ten resources two selective pairs make indiscriminate full exchange
    inefficient.  Orientations counterbalance resource names.
    """
    if resource_count not in {2, 6, 10}:
        raise ValueError(
            "heterogeneous_complexity_scaling supports 2, 6, or 10 resources"
        )

    markets = []
    for (profile_name, pair_specs), orientation in itertools.product(
        HETEROGENEOUS_COMPLEXITY_PROFILES,
        ("xy", "yx"),
    ):
        market = make_nested_market(
            "heterogeneous_complexity_scaling",
            profile_name,
            pair_specs,
            orientation,
            resource_count,
        )
        market.update({
            "complexity_pair_roles": list(
                HETEROGENEOUS_COMPLEXITY_PAIR_ROLES[: resource_count // 2]
            ),
            "nested_family": profile_name,
            "complexity_level": resource_count,
        })
        markets.append(market)
    return markets


# ---------------------------------------------------------------------------
# Broad mechanism map: role x quantity x extraction pressure
# ---------------------------------------------------------------------------


def make_extraction_region_grid(resource_count: int) -> list[dict[str, Any]]:
    """Cross extraction role, inventory quantity, and normalized preferences.

    The panel is selected entirely from payoff geometry, never from model
    outcomes.  A always proposes first, so ``proposer`` means A and
    ``responder`` means B.  The symmetric arm gives both agents the same value
    gap.  Orientation is balanced but economically irrelevant.
    """
    markets = []
    reversed_pairs = tuple(reversed(EXTRACTION_REGION_VALUE_PAIRS))
    for role_index, role in enumerate(EXTRACTION_ROLES):
        for quantity_index, quantity in enumerate(range(2, 7)):
            for profile_index, (steep, flat) in enumerate(
                zip(EXTRACTION_REGION_VALUE_PAIRS, reversed_pairs)
            ):
                if role == "responder":
                    a_values, b_values = steep, flat
                    extractors = ("B",)
                elif role == "proposer":
                    a_values, b_values = flat, steep
                    extractors = ("A",)
                else:
                    a_values = b_values = steep
                    extractors = ("A", "B")

                orientation = (
                    "xy"
                    if (role_index + quantity_index + profile_index) % 2 == 0
                    else "yx"
                )
                label = f"pressure_{profile_index + 1}"
                market = make_market(
                    f"{role}_{label}",
                    f"{role}_{label}",
                    orientation,
                    quantity,
                    resource_count,
                    scale_responder_values(a_values, resource_count),
                    scale_responder_values(b_values, resource_count),
                )
                opportunities = {
                    actor: extraction_opportunity(market, actor)
                    for actor in extractors
                }
                pressure = max(
                    opportunity["normalized_appropriation_advantage"]
                    for opportunity in opportunities.values()
                )
                market.update({
                    "market_panel": "extraction_region_grid",
                    "market_id": (
                        f"region-{role}__profile{profile_index + 1}"
                        f"__q{quantity}_1__resources{resource_count}__{orientation}"
                    ),
                    "type_pair": f"region-{role}__profile{profile_index + 1}",
                    "designed_first_actor": "A",
                    "extraction_role": role,
                    "profile_index": profile_index + 1,
                    "normalized_base_values_a": list(a_values),
                    "normalized_base_values_b": list(b_values),
                    "extraction_pressure": pressure,
                    "extraction_opportunities": opportunities,
                })
                markets.append(market)
    return markets


# ---------------------------------------------------------------------------
# Metadata and validation shared by all active panels
# ---------------------------------------------------------------------------


def _annotate_role_design(
    markets: list[dict[str, Any]],
    panel: str,
    resource_count: int,
) -> None:
    """Record the required starting role and resource-value expansion."""
    definition = market_panel(panel)
    if not definition.requires_first_mover_a:
        return

    for market in markets:
        market["market_panel"] = panel
        market["designed_first_actor"] = "A"
        if resource_count > 2 and definition.record_value_expansion:
            market["value_expansion_scale"] = resource_count // 2


def _validate_constant_extraction(markets: list[dict[str, Any]]) -> None:
    """Ensure the resource-count ablation keeps extraction severity fixed."""
    for market in markets:
        opportunity = responder_extraction_opportunity(market)
        retention = opportunity["welfare_retention"]
        if abs(retention - CONSTANT_EXTRACTION_TARGET) > (
            CONSTANT_EXTRACTION_TOLERANCE + 1e-12
        ):
            raise ValueError(
                f"{market['market_id']} has extraction retention "
                f"{retention:.3f}, outside the frozen target band"
            )
        market["extraction_opportunity"] = opportunity


def _validate_heterogeneous_complexity(
    markets: list[dict[str, Any]], resource_count: int
) -> None:
    """Validate and record economic diagnostics for the resource-count study."""
    for market in markets:
        if len(set(market["utility_a"].values())) != resource_count or len(
            set(market["utility_b"].values())
        ) != resource_count:
            raise ValueError(f"{market['market_id']} does not have strict rankings")

        efficient_trade = welfare_maximizing_trade(market)
        efficient_a, efficient_b = gains(market, efficient_trade)
        first_best = first_best_gain(market)
        if efficient_a <= 0 or efficient_b <= 0:
            raise ValueError(f"{market['market_id']} first best is not strictly IR")
        if efficient_a + efficient_b != first_best:
            raise ValueError(f"{market['market_id']} first-best calculation disagrees")

        simple_trade = full_exchange_trade(market)
        simple_a, simple_b = gains(market, simple_trade)
        extraction = best_strictly_ir_trade(market, "B")
        market.update({
            "first_best_trade": efficient_trade,
            "first_best_gain_a": efficient_a,
            "first_best_gain_b": efficient_b,
            "simple_abundant_exchange_trade": simple_trade,
            "simple_abundant_exchange_gain_a": simple_a,
            "simple_abundant_exchange_gain_b": simple_b,
            "simple_abundant_exchange_welfare_retention": (
                (simple_a + simple_b) / first_best
            ),
            "extraction_opportunity": extraction,
        })

        extraction_retention = extraction["welfare_retention"]
        if resource_count == 2 and not 0.47 <= extraction_retention <= 0.57:
            raise ValueError(f"{market['market_id']} has a weak extraction core")
        if resource_count == 6:
            if simple_a + simple_b != first_best:
                raise ValueError(
                    f"{market['market_id']} six-resource full exchange is not first best"
                )
        if resource_count == 10:
            simple_retention = market[
                "simple_abundant_exchange_welfare_retention"
            ]
            if not 0.43 <= simple_retention <= 0.72:
                raise ValueError(
                    f"{market['market_id']} simple exchange retention is "
                    f"{simple_retention:.3f}"
                )


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


MARKET_CORE_FIELDS = (
    "market_id", "resources", "inventory_a", "inventory_b", "utility_a", "utility_b",
)
MARKET_GROUPING_FIELDS = (
    "extraction_role", "type_pair", "orientation", "profile_index", "extraction_pressure",
)


def split_market_record(market: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate playable inputs and grouping fields from evaluator metadata.

    Generators retain construction diagnostics for validation. Exported market
    files keep only the inputs needed for play, selection, and analysis; the
    companion metadata file preserves everything else under the same market ID.
    """
    record = {key: market[key] for key in MARKET_CORE_FIELDS}
    record.update({key: market[key] for key in MARKET_GROUPING_FIELDS if key in market})
    metadata = {"market_id": market["market_id"]}
    metadata.update({key: value for key, value in market.items() if key not in record})
    return record, metadata


def generate_markets(
    abundant_quantity: int = ABUNDANT_QUANTITY,
    resource_count: int = 2,
    panel: str = "extraction_region_grid",
) -> list[dict[str, Any]]:
    """Build one named, deterministic market panel."""
    market_panel(panel)

    if panel == "responder_extraction_crossed":
        markets = _generate_responder_extraction_crossed(resource_count)
    elif panel in {"responder_extraction", "responder_extraction_holdout"}:
        markets = _generate_responder_extraction(
            panel, abundant_quantity, resource_count
        )
    elif panel == "proposer_extraction":
        markets = _generate_proposer_extraction(abundant_quantity, resource_count)
    elif panel == "symmetric_extraction":
        markets = _generate_symmetric_extraction(abundant_quantity, resource_count)
    elif panel == "constant_extraction_scaling":
        markets = _generate_constant_extraction_scaling(resource_count)
    elif panel == "heterogeneous_complexity_scaling":
        markets = _generate_heterogeneous_complexity_scaling(resource_count)
    elif panel == "extraction_region_grid":
        markets = make_extraction_region_grid(resource_count)
    elif panel == "calibration_holdout60_v1":
        from .calibration_holdout import generate_holdout
        markets = generate_holdout(resource_count)
    else:  # Defensive: MARKET_PANELS should make this unreachable.
        raise AssertionError(f"panel has no generator: {panel}")

    _annotate_role_design(markets, panel, resource_count)
    if panel == "constant_extraction_scaling":
        _validate_constant_extraction(markets)
    elif panel == "heterogeneous_complexity_scaling":
        _validate_heterogeneous_complexity(markets, resource_count)
    return markets
