# Single-good bargaining

A buyer and seller negotiate the price of one good. We vary what each knows
about the other's value or cost and how many messages they can exchange.
Install dependencies using the [root setup guide](../../README.md#start-here).

## Run the main study

```bash
cd experiments/single_good_bargaining
bash scripts/main/gpt51.sh execute --messages 4 --budget 10
```

This runs 30 market instances under five information conditions, including
buyer-only and seller-only disclosure, with a $10 budget. Each condition has
15 buyer-first and 15 seller-first negotiations. `--messages 4` allows four
messages in total across both agents; the default is six.

Add `--condition-set main` to run only least, coarse, and full information
(90 negotiations). Each model has a script in `scripts/main/`. You can also
select a model through `run.sh`, for example:

```bash
bash run.sh gpt6sol execute --condition-set main --messages 6 --budget 12
```

Results, summaries, and readable conversations are saved in `results/`;
three-condition studies use `results/conditions_main/`. Repeat the same command
to resume an interrupted run. Use a new directory when changing the settings or
code. Requests already in progress can take spending beyond the budget.

## Message limits

```bash
bash scripts/ablations/horizons.sh gpt51 execute --budget 10
```

This runs the two-, four-, and six-message studies, with a separate $10 budget
for each. The runner uses seed 20260824, low reasoning, and a 4,096-token limit.

To read the buyer's prompt under coarse information:

```bash
python -m src.experiment prompt --condition coarse --actor buyer --messages 4
```

## Mixed models

```bash
python run_mixed_models.py --pair sonnet5_gemini \
  --execute --budget-usd 10 --results-dir results/mixed/example
```

This runs 180 six-message negotiations: 30 markets, three information conditions,
and both model-role assignments. Both starting roles are included. Use `--help`
to see the available pairs.

## Code and results

| Purpose | File |
|---|---|
| Market data | `data/main_markets.jsonl` |
| Market generation and conditions | `src/design.py` |
| Prompts, legal actions, and negotiation | `src/game.py` |
| Model and generation settings | `../../experiment_runtime/config/` |
| Model adapter | `src/models.py` |
| Single-model runner | `src/run.py` |
| Mixed-model runner | `src/run_pair.py` |
| Validation and summaries | `src/verify.py` |
| Figures | `src/figures.py` |

Each run produces validation checks, summaries, and readable conversations.
To generate figures from the default same-model results:

```bash
python -m src.figures
```

This reads `results/five_per_profitable_stratum_both_starters_v1/seed20260824_n30/`
and writes plots and numerical tables to `output/`.
