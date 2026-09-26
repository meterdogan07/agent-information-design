"""Human-readable prompts for the active double-auction protocol.

This module contains text only. Market generation, information routing, and
clearing remain in :mod:`double_auction.market`.
"""

PAIRWISE_TRADING_PROCEDURE = """After everyone submits a price:
- Bids are sorted from highest to lowest and asking prices from lowest to highest.
- The highest bidder is matched with the cheapest seller whose asking price it can cover,
  followed by the next compatible pair.
- Each matched pair trades at the midpoint of its bid and asking price."""

UNIFORM_PRICE_PROCEDURE = """After everyone submits a price:
- Bids are sorted from highest to lowest and asking prices from lowest to highest.
- The auction trades the largest number q for which the q-th bid covers the q-th ask.
- The q highest bidders trade with the q lowest-cost sellers.
- Every matched participant trades at one uniform price: the midpoint of the
  marginal accepted bid and marginal accepted ask."""

MARGIN_AUCTION_RULES = """Buyer bids are considered from highest to lowest,
while seller asking prices are considered from lowest to highest. The auction
starts with the highest bid and the lowest asking price, and continues matching
participants while a bid is at least as high as an asking price. Each successful
pair trades at the midpoint of its two submitted prices."""

def public_prior_for(market):
    """Keep generator strata and endpoint distributions out of agent prompts."""
    del market
    return ""

ORDER_BOUND_TEXT = {
    "buyer": "Your bid must be between 0 and your value. Otherwise it is invalid.",
    "seller": "Your asking price must be at least your cost. Otherwise it is invalid.",
}

BID_PROMPT = """You are buyer {agent}. You would like to buy one unit of the
item being sold in this double auction. The item is worth {value} to you.
If you buy it at price p, your payoff is {value} - p. If you do not buy it,
your payoff is 0.

{structure}

{trading_procedure}

{order_rule}

Information available to you:
{visible_info}

{round_context}

Submit a bid to maximize your expected payoff.

Return only this JSON object:
{{"reasoning": "1 sentence", "bid": <number>}}
"""

ASK_PROMPT = """You are seller {agent}. You can sell one unit of the item being
traded in this double auction. Supplying the item costs you {cost}.
If you sell it at price p, your payoff is p - {cost}. If you do not sell it,
your payoff is 0.

{structure}

{trading_procedure}

{order_rule}

Information available to you:
{visible_info}

{round_context}

Submit an asking price to maximize your expected payoff.

Return only this JSON object:
{{"reasoning": "1 sentence", "ask": <number>}}
"""


def side_order(endpoints, label, include_numbers=False):
    """Render one side's ranking without comparing values with costs."""
    endpoints = [(number, f"{agent} {label}") for agent, number in endpoints.items()]
    endpoints.sort(key=lambda item: (item[0], item[1]))
    parts = []
    for index, (number, label) in enumerate(endpoints):
        if index:
            parts.append(" = " if number == endpoints[index - 1][0] else " < ")
        parts.append(f"{label}={number}" if include_numbers else label)
    return "".join(parts)


def information_text(condition, market, role):
    """Return exactly what this role learns about other agents."""
    buyer_ranking = side_order(market["values"], "value")
    seller_ranking = side_order(market["costs"], "cost")
    shared_ranking = (
        "Everyone sees the rankings within each side, from lowest to highest:\n"
        f"Buyer values: {buyer_ranking}\n"
        f"Seller costs: {seller_ranking}"
    )
    coarse = (
        shared_ranking
        + "\nThe two rankings are separate: they do not show how any buyer value "
        "compares with any seller cost. Exact numbers are not shown."
    )
    full = (
        shared_ranking
        + "\n\nEveryone also sees the exact endpoints:\n"
        + "Buyer values: "
        + side_order(market["values"], "value", include_numbers=True)
        + "\nSeller costs: "
        + side_order(market["costs"], "cost", include_numbers=True)
        + "\n"
        "These are values and costs, not the bids and asking prices that participants "
        "will submit."
    )
    if condition == "least_information":
        own_endpoint = "value" if role == "buyer" else "cost"
        if market.get("market_design") == "overlap_sweep":
            return (
                f"You know only your own {own_endpoint}. You are not shown the "
                "number or identities of other participants, any market "
                "distribution, anyone else's value or cost, or any ranking."
            )
        return (
            f"You know only your own {own_endpoint} and the participant names. "
            "You are not shown anyone else's value or cost, or any ranking of them."
        )
    if condition == "coarse_information":
        return coarse
    if condition == "full_information":
        return full

    if condition not in {"buyers_full_information", "sellers_full_information"}:
        raise ValueError(f"unknown information condition: {condition}")
    informed_role = "buyer" if condition == "buyers_full_information" else "seller"
    informed_side = "buyers" if informed_role == "buyer" else "sellers"
    other_side = "sellers" if informed_role == "buyer" else "buyers"
    policy = (
        f"Everyone knows that {informed_side} see all exact values and costs, "
        f"while {other_side} see only their ordering. "
    )
    return policy + (full if role == informed_role else coarse)


def round_text(round_number, rounds, feedback_policy, previous_order, feedback):
    if rounds == 1:
        return "This is the only round. The market clears after everyone submits."
    lines = [
        f"Round {round_number} of {rounds}.",
        "Orders before the final round are provisional. Only the final orders "
        "are used for trade and payment.",
    ]
    if round_number == 1:
        lines.append("There is no earlier order or feedback.")
    else:
        lines.append(f"Your previous provisional order was {previous_order}.")
        lines.append(f"Feedback from the previous round: {feedback}")
        if feedback_policy != "none":
            lines.append(
                "This does not guarantee a final trade because everyone may "
                "revise their order."
            )
    return "\n".join(lines)


def build_prompt(condition, market, agent, role, round_number=1, rounds=1,
                 feedback_policy="none", previous_order=None, feedback=None):
    buyers, sellers = ", ".join(market["buyers"]), ", ".join(market["sellers"])
    capacity = min(len(market["buyers"]), len(market["sellers"]))
    public_prior = public_prior_for(market)
    trading_procedure = (
        MARGIN_AUCTION_RULES
        if market.get("market_design") in {"margin_sweep", "margin_sweep_mirrored"}
        else UNIFORM_PRICE_PROCEDURE if market.get("mechanism") == "uniform_price"
        else PAIRWISE_TRADING_PROCEDURE
    )
    if market.get("market_design") == "overlap_sweep" and condition == "least_information":
        structure = """Market:
- Other buyers and sellers participate, but their number and identities are not disclosed.

All participants choose their prices independently during this round. You must
choose your price without seeing any bid or asking price submitted by another
participant. After everyone has submitted, the auction compares the orders and
determines which trades take place."""
    else:
        prior_block = f"\n{public_prior}\n" if public_prior else ""
        structure = f"""Market:
- Buyers: {buyers}. Sellers: {sellers}.
- Each buyer can buy one unit and each seller can sell one unit.
- At most {capacity} trades can occur.
{prior_block}
All participants choose their prices independently during this round. You must
choose your price without seeing any bid or asking price submitted by another
participant. After everyone has submitted, the auction compares the orders and
determines which trades take place."""
    common = {
        "structure": structure,
        "trading_procedure": trading_procedure,
        "order_rule": ORDER_BOUND_TEXT[role],
        "visible_info": information_text(condition, market, role),
        "round_context": round_text(
            round_number, rounds, feedback_policy, previous_order, feedback,
        ),
    }
    if role == "buyer":
        return BID_PROMPT.format(
            agent=agent, value=market["values"][agent], **common,
        )
    return ASK_PROMPT.format(
        agent=agent, cost=market["costs"][agent], **common,
    )
