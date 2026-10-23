# Single-good bargaining

Install dependencies using the [root setup guide](../README.md#start-here).

Activate the environment, then preview a four-message negotiation study:

```bash
conda activate llm-information-design
cd single_good_bargaining
bash scripts/main/gpt51.sh --messages 4
```

This previews 30 market instances × 5 information conditions
= 150 negotiations. Each model has its own script in `scripts/main/`;
`--messages 4` sets the maximum total number of messages, counting both agents'
turns, not four rounds. The default is six messages. No API calls are made.

To start a paid run, explicitly add `execute` and a dollar budget:

```bash
bash scripts/main/gpt51.sh execute --messages 4 --budget 10
```

Run the command without `execute` first to see the settings and estimated cost,
without calling any models. Then choose a budget for the paid run; $10 above is
only an example. Results, summaries, and readable conversations are saved in
`results/`.

### Other message limits

To preview the main study's two-, four-, and six-message settings together:

```bash
bash scripts/ablations/horizons.sh gpt51
```

This runs the 2-, 4-, and 6-message settings separately.
The runner uses seed 20260824, a 4,096-token limit, and low reasoning effort.

Add `execute --budget 10` to run the sweep. The budget applies separately to each
message limit, not to the combined study. The shorter entry point also works:
`bash run.sh gpt51 --messages 4`.

### Optional: inspect a prompt

This displays the buyer's prompt under coarse information without running a model:

```bash
python -m src.experiment prompt --condition coarse --actor buyer --messages 4
```

## Mixed models

```bash
python run_mixed_models.py --pair sonnet5_gemini --results-dir results/mixed/example
```

This previews 30 markets × 3 conditions × 2 model-role assignments = 180 negotiations at six messages, retaining both starting roles. Add `--execute --budget-usd <authorized_amount>` for paid execution or --mock --limit 2` for an offline smoke test.

## Where to read the code


| Purpose                                 | File                             |
| --------------------------------------- | -------------------------------- |
| Market data                             | `data/main_markets.jsonl`        |
| Market generation and conditions        | `src/design.py`                  |
| Prompts, legal actions, and negotiation | `src/game.py`                    |
| Model and generation settings           | `../experiment_runtime/config/models.json`, `../experiment_runtime/config/generation.json` |
| Study-specific model adapter             | `src/models.py` |
| Single-model runner                     | `src/run.py`                     |
| Mixed-model runner and settings         | `src/run_pair.py`                |
| Validation and summaries                | `src/verify.py`                  |
| Figure generation                       | `src/figures.py`                 |

The `src/` directory contains the implementation of the game. Runners audit and summarize new runs automatically and export readable conversations. Generated outputs are local and excluded from Git.

After completing the same-model study, generate figures from the saved results:

```bash
python -m src.figures
```

This reads `results/five_per_profitable_stratum_both_starters_v1/seed20260824_n30/`
and writes plots and numerical tables to `output/`.
