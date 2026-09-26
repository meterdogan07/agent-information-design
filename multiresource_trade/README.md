# Multi-resource trade

Install dependencies using the [root setup guide](../README.md#start-here).

All commands in this section run from `multiresource_trade/`.
Previews make no API calls, but may save a generated market copy in an ignored
data subdirectory. The published input files remain unchanged.

```bash
conda activate llm-information-design
cd multiresource_trade
bash scripts/main/gpt51.sh
```

This previews 90 markets × 3 information conditions = 270 negotiations per
model. The script runs coarse/full and least information in separate output
directories. It uses eight messages, 4,096 completion tokens, low reasoning,
and the negotiation prompt. Eight messages means eight total turns across both
agents, not eight rounds. Presets for the main study are `gpt5mini`, `gpt51`,
`gemini`, `claudesonnet5`, and `grok43`.

Each main-study model has its own script in `scripts/main/`. For example,
`gemini.sh` runs Gemini and `claudesonnet5.sh` runs Sonnet.

**Paid execution:** add `execute`, e.g. `bash scripts/main/gpt51.sh execute`. The launcher has no
hard dollar cap; preview first and set provider-side limits.
`WORKERS=1` limits concurrency; `RESULT_ROOT=results/my_run` chooses an output
root. Never reuse a completed output directory for a different configuration.

Inspecting the prompt is optional: `bash run.sh prompts` displays an opening
prompt without running a model. For advanced options, use
`python -m src.trading.run --help`.

Supporting studies, also previewed by default in these examples:

```bash
bash scripts/ablations/dialogue.sh gpt51
bash scripts/ablations/resources.sh gpt51
bash scripts/ablations/mediator.sh gpt51
```

These use six dialogue markets, nested 2/6/10-resource markets, and 30 mediator
markets. Resource expansion also changes available exchanges. The mediator
maximizes total gains, subject to both agents accepting. Launchers use the
current prompt, not retired variants.

Add `execute` after the model name for paid runs. Each study script contains
its own market and horizon settings. `scripts/_common.sh` supplies shared
runner arguments; the repository's `config/models.json` holds model IDs and providers,
and `config/generation.json` holds generation defaults for all three games.
The older `bash run.sh MODEL STUDY MODE` syntax remains supported.

## Mixed models

```bash
python run_mixed_models.py --pair sonnet5_gemini --results-dir results/mixed/example
```

This previews 90 markets × 3 conditions × 2 role assignments = 540 negotiations.
Use `--pair gpt51_sonnet5` for GPT-5.1 × Sonnet; `--help` lists other pairs.
Add `--execute --budget-usd <authorized_amount>` for paid execution, or
`--mock --limit 2` for an offline check.

GPT-5.1 × Grok retains its balanced-assignment runner:

```bash
python run_balanced_pair.py --pair gpt51_grok43 --markets 90 \
  --results-dir results/mixed/gpt51_grok43_original
python run_balanced_pair.py --pair gpt51_grok43 --markets 90 --reverse-assignments \
  --results-dir results/mixed/gpt51_grok43_reversed
```

Both are previews. Each covers 270 negotiations; together they cover both
assignments on all 90 markets. The 30-market default is a smaller design.

## Where to read the code


| Purpose                    | File                                                                    |
| -------------------------- | ----------------------------------------------------------------------- |
| Model/provider settings    | `../config/models.json` and `../config/generation.json` |
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

## Invalid-action welfare robustness check

The main results retain all negotiations. The separate robustness analysis
excludes both coarse and full negotiations for a matched market if either
contains a recorded action error. It does not delete logs, remove individual
turns, or exclude a pair solely because its least-information run has errors.
Markets are matched within model, horizon, first mover, and replication.

From this directory, supply the main-study results root containing one folder
per model, each with `least/episodes.jsonl` and
`coarse_and_full/episodes.jsonl`:

```bash
python -m src.analysis.invalid_action_robustness /path/to/01_main_same_model \
  --output results/invalid_action_robustness.json
python -m unittest discover -s tests -p 'test_invalid_action_robustness.py'
```

The JSON reports per-condition invalid-action counts, excluded market IDs,
and coarse/full normalized welfare before and after exclusion, overall and
within each utility-defined opportunity group. Empty filtered groups return
`null`, not zero. Input hashes identify the exact source files; incomplete,
duplicate, or unmatched coarse/full observations raise an error. The output
path must be new. Ordinary `metrics.analyze` summaries also report invalid
action and affected-negotiation counts without changing the primary sample.
