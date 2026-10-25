#!/usr/bin/env python
"""Human-readable entry point; this command never calls an API."""

from __future__ import annotations

import argparse

from . import design, game


def fixed_markets(
    panel: str = design.PROFITABLE_MIRRORED_PANEL_ID,
) -> list[dict]:
    count = (
        design.PROFITABLE_MIRRORED_MARKET_COUNT
        if panel == design.PROFITABLE_MIRRORED_PANEL_ID
        else design.MARKET_COUNT
    )
    return design.generate_markets(count=count, panel_id=panel)


def describe(panel: str) -> None:
    markets = fixed_markets(panel)
    families = {
        family: sum(market["family"] == family for market in markets)
        for family in design.FAMILIES
    }
    print("SELF-CONTAINED SINGLE-GOOD BARGAINING")
    print("Population prior: cost c and value v are independently uniform on [20,100].")
    if panel == design.PROFITABLE_MIRRORED_PANEL_ID:
        print("Evaluation panel: five base markets per profitable surplus category, each run under both starters.")
    else:
        print("Evaluation panel: five sampled cases from each ex-post surplus category.")
    print("Agents alternate one standing price offer; a counteroffer replaces it.")
    print("Final message: accept or reject only. An unanswered offer is illegal.")
    print("Trade executes iff c <= price <= v; equality and zero gain are allowed.")
    print("Seller payoff = price-c; buyer payoff = v-price; welfare = v-c.")
    print(f"\nMarkets: {len(markets)} fixed instances, seed {design.MARKET_SEED}")
    starters = {
        mover: sum(market["first_mover"] == mover for market in markets)
        for mover in ("seller", "buyer")
    }
    print(f"Starting order: {starters}")
    print(f"Balanced market families: {families}")
    print(f"Horizons: {design.HORIZONS}")
    print("\nInformation conditions:")
    for condition in design.CONDITIONS:
        signals = design.CONDITION_SIGNALS[condition]
        print(f"- {condition}: {design.CONDITION_DESCRIPTIONS[condition]}")
        print(f"  seller signal={signals['seller']}; buyer signal={signals['buyer']}")
    print("\nAll executable code is in this src directory.")
    print("No API call was made.")


def show_markets(panel: str) -> None:
    for market in fixed_markets(panel):
        print(
            f"{market['market_id']}  cost={market['seller_cost']:>4}  "
            f"value={market['buyer_value']:>4}  {market['family']:<8}  "
            f"{market['first_mover']}-first"
        )


def show_prompt(args: argparse.Namespace) -> None:
    markets = fixed_markets(args.panel)
    if not 0 <= args.market_index < len(markets):
        raise SystemExit(
            f"market index must be between 0 and {len(markets) - 1}"
        )
    market = markets[args.market_index]
    signal = design.signals_for(args.condition)[args.actor]
    print(f"condition={args.condition}; actor={args.actor}; role signal={signal}")
    print(
        game.build_prompt(
            signal,
            args.actor,
            market,
            events=[],
            standing=None,
            turn=1,
            horizon=args.horizon,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--panel",
        choices=design.PANEL_IDS,
        default=design.PROFITABLE_MIRRORED_PANEL_ID,
        help="market panel to inspect (defaults to the main panel)",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("markets", help="print the exact fixed markets")
    prompt = subparsers.add_parser("prompt", help="print one exact opening prompt")
    prompt.add_argument("--condition", choices=design.CONDITIONS, required=True)
    prompt.add_argument("--actor", choices=("seller", "buyer"), required=True)
    prompt.add_argument("--messages", "--horizon", dest="horizon", type=int,
                        choices=design.HORIZONS, required=True,
                        help="Maximum total messages across both agents.")
    prompt.add_argument("--market-index", type=int, default=0)
    args = parser.parse_args()
    if args.command is None:
        describe(args.panel)
    elif args.command == "markets":
        show_markets(args.panel)
    else:
        show_prompt(args)


if __name__ == "__main__":
    main()
