# Running the experiments

Run commands from the repository root using the same Python environment used
for installation. `experiments.py` is the common entry point for the main
same-model studies. Existing scripts remain supported, and no game engine,
prompt, market generator, scoring rule, or saved result is changed.

## Preview first

```bash
python experiments.py models
python experiments.py main trade gpt51
python experiments.py main bargaining gpt51 --messages 6
python experiments.py main auction gpt51
```

All three default to preview and make no model calls. A trade preview may write
a generated market copy. Replace `gpt51` with a preset listed by `models`.
Use `--workers N` for concurrency. The launcher prints the native command and
working directory, so its behavior is transparent.

| Main study | Markets | Conditions | Interaction |
|---|---|---|---|
| Trade | 90 | least, coarse, full | eight messages |
| Bargaining | 30, including both starters | least, coarse, full | two, four, or six messages |
| Auction | 30 | least, coarse, full | simultaneous orders, midpoint clearing |

The common bargaining command explicitly selects the three main conditions.
The older bargaining launcher still defaults to all five, including one-sided
disclosure. This intentional difference is visible in the printed command.
Model endpoints and generation defaults remain in `config/`.

## Paid execution

Only `--mode execute` authorizes calls through this launcher. Preview the exact
configuration first. Trade retains its original lack of a dollar cap; use
provider-side spending controls. Bargaining requires explicit authorization,
but in-flight requests can exceed the threshold.

```bash
python experiments.py main trade gpt51 --mode execute --results-dir results/trade_example
python experiments.py main bargaining gpt51 --mode execute --messages 6 --budget 10 --results-dir results/bargaining_example
```

The illustrative `$10` is not a cost prediction. `--results-dir` is resolved
relative to your current directory. For trade it is an output root containing
model/study subdirectories; for bargaining it is the run directory itself.
Without it, each implementation retains its existing output location.

Auctions retain the frozen-plan/source-commit safeguard. Do not remove this
check merely to make the commands identical. Choose a new label for a new run:

```bash
python experiments.py main auction gpt51 --mode freeze --run-label example --estimated-cost 5
# Review and commit the source and generated plan before execution.
python experiments.py main auction gpt51 --mode execute --run-label example --estimated-cost 5
```

The estimate is not a spending cap. Freezing an existing label does not
overwrite its plan. To continue an interrupted auction, repeat the execute
command with `--resume`; do not freeze it again. Follow the
[auction guide](double_auction/README.md) for plan review and commits.
Trade and bargaining retain their native resume behavior. Never reuse a result
directory after changing scientific settings or source.

## Finding and extending the code

| Responsibility | Location |
|---|---|
| Common main-study command | `experiments.py` |
| Model IDs, provider routing, generation defaults | `config/` |
| Shared provider and execution helpers | `experiment_runtime/` |
| Trade markets, prompts, engine | `multiresource_trade/src/trading/` |
| Trade metrics and transcript exports | `multiresource_trade/src/analysis/` |
| Bargaining markets, prompts, engine, analysis | `single_good_bargaining/src/` |
| Auction markets, prompts, engine, analysis | `double_auction/src/double_auction/` |
| Published market inputs | each experiment's `data/` |
| Offline common-launcher tests | `tests/test_experiment_cli.py` |

Mixed-model runs and supporting studies retain their experiment-local commands
and guides. They are not silently mapped onto main-study defaults. The three
engines remain independent because their rules and outcome measures differ.

## Offline checks

```bash
python -m unittest discover -s tests
python export_markets.py --check
```

Neither command authorizes a paid experiment. Consult each experiment guide for
its additional tests and result-validation commands.
