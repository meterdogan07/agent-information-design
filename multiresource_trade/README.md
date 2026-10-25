# Multi-resource trade

Install dependencies using the [root setup guide](../README.md#start-here).

Two agents exchange resources while negotiating over their individual gains.
The main study compares least, coarse, and full information on 90 markets.

## Run the main study

```bash
cd multiresource_trade
bash scripts/main/gpt51.sh execute
```

This runs 270 negotiations with GPT-5.1. The script saves coarse/full and least information in separate output
directories. It uses eight messages, 4,096 completion tokens, low reasoning,
and the negotiation prompt. Eight messages means eight total turns across both
agents, not eight rounds. Presets for the main study are `gpt5mini`, `gpt51`,
`gemini`, `claudesonnet5`, and `grok43`.

Each main-study model has its own script in `scripts/main/`. For example,
`gemini.sh` runs Gemini and `claudesonnet5.sh` runs Sonnet.

Set spending limits with your API provider.
`WORKERS=1` limits concurrency; `RESULT_ROOT=results/my_run` chooses an output
root. Never reuse a completed output directory for a different configuration.

To read an opening prompt, use `bash run.sh prompts`. For more options, use
`python -m src.trading.run --help`.

## Resource count, dialogue length, and mediation

```bash
bash scripts/ablations/dialogue.sh gpt51 execute
bash scripts/ablations/resources.sh gpt51 execute
bash scripts/ablations/mediator.sh gpt51 execute
```

These use six dialogue markets, nested 2/6/10-resource markets, and 30 mediator
markets. Resource expansion also changes available exchanges. The mediator
maximizes total gains, subject to both agents accepting.

Each study script contains
its own market and horizon settings. `scripts/_common.sh` supplies shared
runner arguments; `experiment_runtime/config/models.json` holds model IDs and providers,
and `experiment_runtime/config/generation.json` holds generation defaults for all three games.
The older `bash run.sh MODEL STUDY MODE` syntax remains supported.

## Mixed models

```bash
python run_mixed_models.py --pair sonnet5_gemini \
  --execute --budget-usd 10 --results-dir results/mixed/example
```

This runs Sonnet and Gemini on 90 markets under three information conditions,
with both role assignments: 540 negotiations in total.
Use `--pair gpt51_sonnet5` for GPT-5.1 × Sonnet; `--help` lists other pairs.
The example sets a $10 budget.

For GPT-5.1 × Grok, use the balanced-assignment runner. The commands below
show the two configurations; add `--execute --budget-usd 10` to run each:

```bash
python run_balanced_pair.py --pair gpt51_grok43 --markets 90 \
  --results-dir results/mixed/gpt51_grok43_original
python run_balanced_pair.py --pair gpt51_grok43 --markets 90 --reverse-assignments \
  --results-dir results/mixed/gpt51_grok43_reversed
```

Each covers 270 negotiations; together they cover both
assignments on all 90 markets. The 30-market default is a smaller design.

## Where to read the code


| Purpose                    | File                                                                    |
| -------------------------- | ----------------------------------------------------------------------- |
| Model/provider settings    | `../experiment_runtime/config/models.json` and `../experiment_runtime/config/generation.json` |
| Inventories and values     | `data/*.jsonl`; additional diagnostics in `data/metadata/`              |
| Market generation          | `src/trading/markets.py`, `market_profiles.py`, `market_core.py`        |
| Agent prompts              | `src/trading/p2p_prompts.py`, `mediator_prompts.py`                     |
| Negotiation and validation | `src/trading/game.py`, `mediator.py`                                    |
| Scoring and behavior       | `src/analysis/metrics.py`, `coordination.py`, `behavior.py`             |

Runners compute summaries automatically. To read saved conversations:

```bash
python -m src.analysis.transcripts <results_dir>
```

The scripts use the same source implementation. The additional entry point
`run_balanced_pair.py` changes role assignment, not the game rules.
