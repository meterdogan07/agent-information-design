# Double auctions

Install dependencies using the [root setup guide](../README.md#start-here).
The optional auction package installation also requires that local runtime;
install the repository root before installing `double_auction/` separately.

Run from `double_auction/`:

```bash
conda activate llm-information-design
cd double_auction
bash scripts/main/gpt51.sh
```

This previews 30 markets × 3 conditions = 90 auctions, with six agent calls
each. Each agent submits once; there is no dialogue or message-limit argument.
The main-paper panel is `margin`, not `information`. All auction runners
in this release default to a 2,048-token completion cap.
Each model has its own script in `scripts/main/`, with the market count,
and seed written explicitly; generation defaults and model presets come from
[`../experiment_runtime/config/`](../experiment_runtime/config/README.md). Existing `run.sh` commands still work.

Paid auction execution requires committed source and a committed frozen plan.
If the downloaded copy has no Git history, initialize it from the release root
first (`git init`, `git add .`, then `git commit`). For an anonymous working
copy, use a repository-local anonymous author name/email, never a global change.

From `double_auction/`, choose a run label and a positive cost estimate:

```bash
bash scripts/main/gpt51.sh freeze \
  --run-label example --estimated-cost 5
git add experiments/market_phases/plans/
git commit -m "Freeze auction experiment plan"
# Paid; use exactly the same scientific and cost-estimate arguments:
bash scripts/main/gpt51.sh execute \
  --run-label example --estimated-cost 5
```

The `5` is an illustrative planning estimate, **not a hard spending cap**.
Frozen plans are immutable; select a new run label when changing parameters.
Use `--resume` only to continue the same run. Keep the 2,048-token cap fixed
across comparisons; an explicit CLI or environment override changes this
setting and must be recorded.

Supporting panels:

```bash
bash scripts/ablations/placement.sh gpt51
bash scripts/ablations/market-size.sh gpt51
```

These scripts use 15 markets and seed 97731. Add `freeze` or `execute` after
the model name, followed by the same run-label and cost-estimate options as above.

The size study uses nested 3×3, 6×6, and 10×10 markets with **uniform-price
clearing**, whereas the main margin study uses pair-specific midpoint prices.
Do not interpret the size comparison as changing only the number of agents.

### One-sided disclosure wording

In the buyer-informed and seller-informed conditions, exact values and costs
are sent only to the designated side; the other side receives rankings.
Both sides are told who receives exact information. However, the informed
side's card retains the sentence "Everyone also sees the exact endpoints"
from the full-information template, which conflicts with that announcement.
The code preserves this wording to reproduce the recorded experiments.
It does not send exact values to the other side, but the ambiguous wording
may affect agents' understanding of what others know. These recipient effects
should be interpreted with that limitation; corrected wording requires a
separately identified experiment, not a silent change to the original prompts.

## Mixed models

```bash
python run_mixed_models.py --pair sonnet5_gemini --mode preview
```

This previews 30 markets × 3 conditions × 2 model-role assignments = 180
auctions. One model plays all buyers and the other all sellers, then they swap.
Use `--help` to list the six pairs.

For paid runs, use `--mode freeze`, commit the plan and source, then use
`--mode execute --confirm-paid-run`. Specify identical `--estimated-cost-usd`
and `--budget-usd` when freezing and executing.

## Where to read the code

Implementation files below are in `src/double_auction/`.


| Purpose                          | File                          |
| -------------------------------- | ----------------------------- |
| Model settings and named studies | `cli.py`                      |
| Market construction and clearing | `market.py`                   |
| Agent prompts                    | `prompts.py`                  |
| Running one auction              | `episode.py`                  |
| Single-model runner              | `runner.py`                   |
| Mixed-model runner and budget    | `mixed.py`, `mixed_budget.py` |
| Scoring and validation           | `analysis.py`, `audit.py`     |
| Readable conversations           | `render.py`                   |

Market inputs are in `data/`.
To audit or inspect a completed run:

```bash
python auction.py audit <results_dir>
python auction.py render <results_dir>
```
