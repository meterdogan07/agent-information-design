"""Diagnostics and aggregate statistics for auction outcomes."""

from __future__ import annotations


def _mean(values):
    return sum(values) / len(values) if values else None


def order_diagnostics(values, costs, bids, asks, first_best_welfare,
                      first_best_quantity):
    """Measure the welfare supported by the submitted order ranking."""
    buyers = sorted(
        ((name, bid) for name, bid in bids.items() if bid is not None),
        key=lambda item: (-item[1], item[0]),
    )
    sellers = sorted(
        ((name, ask) for name, ask in asks.items() if ask is not None),
        key=lambda item: (item[1], item[0]),
    )

    running = candidate_welfare = 0.0
    for (buyer, _), (seller, _) in zip(buyers, sellers):
        running += values[buyer] - costs[seller]
        candidate_welfare = max(candidate_welfare, running)

    def inversions(endpoints, orders):
        agents = [name for name, order in orders.items() if order is not None]
        return sum(
            (endpoints[a] - endpoints[b]) * (orders[a] - orders[b]) < 0
            for index, a in enumerate(agents)
            for b in agents[index + 1:]
            if endpoints[a] != endpoints[b] and orders[a] != orders[b]
        )

    marginal_gap = None
    if len(buyers) >= first_best_quantity and len(sellers) >= first_best_quantity:
        marginal_gap = (
            sellers[first_best_quantity - 1][1]
            - buyers[first_best_quantity - 1][1]
        )

    return {
        "candidate_welfare": candidate_welfare,
        "candidate_efficiency": candidate_welfare / first_best_welfare,
        "marginal_first_best_gap": marginal_gap,
        "buyer_rank_inversions": inversions(values, bids),
        "seller_rank_inversions": inversions(costs, asks),
    }


def summarize(rows):
    """Aggregate one information condition across its matched markets."""
    realized = sum(row["welfare"] for row in rows)
    optimal = sum(row["first_best_welfare"] for row in rows)
    candidate = sum(row["candidate_welfare"] for row in rows)
    buyer_surplus = sum(row["buyer_surplus"] for row in rows)
    seller_surplus = sum(row["seller_surplus"] for row in rows)
    total_surplus = buyer_surplus + seller_surplus

    def mean(field):
        return _mean([row[field] for row in rows if row.get(field) is not None])

    return {
        "n_markets": len(rows),
        "efficiency": realized / optimal,
        "candidate_efficiency": candidate / optimal,
        "normalized_implementation_loss": (candidate - realized) / optimal,
        "realized_welfare": realized,
        "first_best_welfare": optimal,
        "candidate_welfare": candidate,
        "implementation_loss": candidate - realized,
        "trades_per_market": mean("trade_count"),
        "first_best_trades_per_market": _mean([
            len(row["first_best_trades"]) for row in rows
        ]),
        "mean_buyer_shading": mean("mean_buyer_shading"),
        "mean_seller_markup": mean("mean_seller_markup"),
        "mean_buyer_revision": mean("mean_buyer_revision"),
        "mean_seller_revision": mean("mean_seller_revision"),
        "buyer_surplus": buyer_surplus,
        "seller_surplus": seller_surplus,
        "buyer_surplus_share": (
            buyer_surplus / total_surplus if total_surplus else None
        ),
        "seller_surplus_share": (
            seller_surplus / total_surplus if total_surplus else None
        ),
        "mean_marginal_first_best_gap": mean("marginal_first_best_gap"),
        "buyer_rank_inversions": sum(row["buyer_rank_inversions"] for row in rows),
        "seller_rank_inversions": sum(row["seller_rank_inversions"] for row in rows),
        "invalid_orders": sum(row["invalid_order_count"] for row in rows),
        "all_round_invalid_orders": sum(
            row["all_round_invalid_orders"] for row in rows
        ),
        "unparsed_orders": sum(row["unparsed_orders"] for row in rows),
    }


def summarize_by_market_family(rows):
    """Summarize the predefined thin, intermediate, and thick markets."""
    groups = {}
    for row in rows:
        groups.setdefault(row["market_family"], []).append(row)
    return {key: summarize(groups[key]) for key in sorted(groups)}
