# Double auctions

Three buyers and three sellers each submit a price. The main study runs
30 markets under least, coarse, and full information: 90 auctions in total.
Each auction uses six model calls, with no dialogue.
Install dependencies using the [root setup guide](../README.md#start-here).

## Run the main study

Auctions save their configuration in a frozen plan before running. Commit the
plan and experiment code so the run can be traced back to its settings.
Start from a Git clone with your code changes committed, then run:

```bash
cd double_auction
bash scripts/main/gpt51.sh freeze \
  --run-label example --estimated-cost 5
git add experiments/market_phases/plans/
git commit -m "Freeze auction experiment plan"
bash scripts/main/gpt51.sh execute \
  --run-label example --estimated-cost 5
```

The cost estimate is recorded with the plan; it does not limit spending.
To resume, repeat the execute command with `--resume`. Use a new run label
when changing the configuration.

The main study is named `margin` and uses a 2,048-token completion limit.
Model settings are in [the shared configuration](../experiment_runtime/config/README.md).
For models without a shell script, use:

```bash
python auction.py study gpt6sol margin freeze \
  --markets 30 --seed 97617 --run-label sol_example --estimated-cost 5
```

Commit the plan, then repeat with `execute` in place of `freeze`.

## Information recipients and market size

These scripts select the corresponding studies:

```bash
bash scripts/ablations/placement.sh gpt51 freeze --run-label placement_example --estimated-cost 5
bash scripts/ablations/market-size.sh gpt51 freeze --run-label size_example --estimated-cost 5
```

Commit the generated plans and repeat with `execute`. Both scripts use
15 markets and seed 97731. The size study compares nested 3×3, 6×6, and 10×10
markets using uniform-price clearing; the main study uses midpoint pricing.
The size comparison therefore changes both market size and pricing rules.

### A note on one-sided disclosure

Only the designated side receives exact values and costs; the other side gets
rankings. Both are told who is informed. The informed side's card also says
"Everyone also sees the exact endpoints," inherited from the full-information
template. This conflicts with the announcement and may affect how agents
interpret the disclosure. We preserve the wording used in the experiments;
changing it should be treated as a separate study.

## Mixed models

Each model pair runs 180 auctions: 30 markets, three conditions, and two role
assignments. One model plays all buyers and the other all sellers, then they swap.
Use `python run_mixed_models.py --help` to see the available pairs and options.

Select a pair with `--pair sonnet5_gemini`, for example. First use `--mode freeze`
and commit the plan and source, then use `--mode execute --confirm-paid-run`.
Keep `--estimated-cost-usd` and `--budget-usd` unchanged between the two commands.

## Code and results

Implementation files are in `src/double_auction/`.

| Purpose | File |
|---|---|
| Model settings and named studies | `cli.py` |
| Market construction and clearing | `market.py` |
| Agent prompts | `prompts.py` |
| Running one auction | `episode.py` |
| Single-model runner | `runner.py` |
| Mixed-model runner and budget | `mixed.py`, `mixed_budget.py` |
| Scoring and validation | `analysis.py`, `audit.py` |
| Readable conversations | `render.py` |

Market inputs are in `data/`. To check a completed run and export its conversations:

```bash
python auction.py audit <results_dir>
python auction.py render <results_dir>
```
