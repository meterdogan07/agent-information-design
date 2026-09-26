# Empirical Information Design for LLM Agents

**How does what an agent knows change what agents achieve together?**

This repository accompanies our study of information disclosure in
three economic settings. We compare disclosure conditions on the same markets,
keeping the models and interaction rules fixed, and examine both the
opportunities agents identify and the outcomes they achieve.

[Getting started](#getting-started) · [Experiments](#exploring-the-experiments) ·
[Markets](#the-markets) · [Analysis](#understanding-the-results) · [Reproducibility](#reproducibility)

Each setting has its own implementation and guide, covering the markets,
prompts, model settings, and analysis.


| Setting                | What the agents do                                       | Guide                                          |
| ---------------------- | -------------------------------------------------------- | ---------------------------------------------- |
| Multi-resource trade   | Two agents negotiate an exchange of resources.           | [Trade](multiresource_trade/README.md)         |
| Single-good bargaining | A buyer and seller negotiate the price of one item.      | [Bargaining](single_good_bargaining/README.md) |
| Double auctions        | Multiple buyers and sellers submit prices independently. | [Auctions](double_auction/README.md)           |

<a id="start-here"></a>

## Getting started

The code uses Python 3.11 or later, Bash, and Git, and can be used on macOS,
Linux, or WSL. The following commands set up the environment and check that
the market files and bargaining simulation work locally:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[analysis]'
bash check.sh
```

These checks use simulated responses, so you can explore the setup without
API credentials or paid model calls.

### Connecting models

When you are ready to use the models, their API keys belong in a `.env` file
at the repository root. Only the providers used by your chosen experiment
need credentials:

```dotenv
OPENAI_API_KEY=your_key
OPENROUTER_API_KEY=your_key
```

The `.env` file is ignored by Git and should remain private. If it does not
exist, `touch .env` creates an empty one without overwriting an existing file.

## Exploring the experiments

The scripts under each experiment's `scripts/main/` launch its main study for each
model, while `scripts/ablations/` contains the supporting studies. For example,
you can preview the GPT-5.1 trade experiment and its resource-count study with:

```bash
cd multiresource_trade
bash scripts/main/gpt51.sh
bash scripts/ablations/resources.sh gpt51
```

Neither command makes a model call. The other main-study scripts are
`gpt5mini.sh`, `gemini.sh`, `claudesonnet5.sh`, and `grok43.sh`; the `run.sh`
entry points are also available.

Interactions between different models are handled by `run_mixed_models.py`
in each experiment directory. These studies cover all six pairs of GPT-5.1,
Sonnet, Gemini, and Grok, with each model taking each role.

Same-model and mixed-model studies use the same game engine and prompts within
each setting. Their entry points retain the study-specific settings and output
formats. Trade and bargaining share the mixed-run scheduling, budget accounting,
role routing, and replay checks in `experiment_runtime/negotiations.py`; the
balanced trade launcher uses the same execution helpers. Auction runners share
`double_auction/src/double_auction/episode.py`, retaining their separate frozen-plan
and budget rules.

> [!IMPORTANT]
> Follow the relevant experiment guide before starting a paid run. Keep
> `API_RUN_BUDGET` unset when previewing bargaining; setting it authorizes
> execution. Auctions require a frozen plan before execution. Provider
> availability and prices can change, so recorded costs are estimates.

A run can be resumed with its original configuration; changes to the code or
settings should use a new output directory. Mixed-model runners lock that
directory to prevent duplicate execution.

## The markets

The market definitions are saved in each experiment's `data/` directory as
JSONL files, with one market per line. The main experiments use:

- **[Trade](multiresource_trade/data/main_markets.jsonl)** uses 90 two-resource
  market records. Recomputing extraction opportunities from utilities gives
  30 records each with greater opportunity for A, greater opportunity for B,
  or equal opportunities. Negotiations allow eight messages, with A starting.
- **[Bargaining](single_good_bargaining/data/main_markets.jsonl)** uses 15 cost–value
  pairs across three surplus groups, each used with both starting roles.
  This gives 30 instances at each message limit of two, four, or six.
- **[Auctions](double_auction/data/main_markets.jsonl)** uses 30 markets across three
  margin groups, each with three buyers and three sellers. Orders are
  simultaneous, and completed trades use midpoint pricing.

Reusing the same markets lets us compare disclosure conditions without
changing the underlying trading opportunities. The files include the true
utilities, values, and costs used to evaluate outcomes, but agents receive
only the information allowed by their condition. The experiment guides
describe these conditions, including one-sided disclosure.

For trade, construction notes and extraction calculations are kept in
[data/metadata/](multiresource_trade/data/metadata/), separate from the market
records and linked by `market_id`.

<details>
<summary>Interpreting trade records and group labels</summary>

The 90 trade records contain 60 distinct inventory–utility configurations.
The A-higher and B-higher groups each contain 15 distinct configurations,
represented twice; the equal-opportunity group contains 30.

The stored `extraction_role` field identifies a construction category, not
necessarily the agent with greater extraction opportunity. The paper's
grouped figures compare both agents' opportunities from their utilities.
The current summary code still groups by the stored label, so its `by_role`
output should not be interpreted as the paper's A-higher/B-higher comparison.

</details>

[MARKETS.json](MARKETS.json) lists the files, counts, and checksums. You can
check that the saved markets match their generators without calling a model:

```bash
python export_markets.py --check
```

## Understanding the results

The analysis distinguishes finding a useful agreement from reaching it using
three measures:

- **Candidate quality (Q)** scores the best qualifying proposal in trade and
  bargaining. In auctions, it scores the gains available from profitable pairs
  formed by the submitted price rankings.
- **Realized performance (W)** scores the final outcome, including failure to trade.
- **Realization gap (L = Q − W)** measures the difference between the two.

Trade and auctions measure normalized total gains; bargaining uses the
normalized Nash product to capture agreement and balance. We analyze the
three settings separately rather than pooling their scores. Market groups,
starting roles, and averaging weights also matter when comparing results.

Each experiment guide points to the scoring and plotting code and explains
how to inspect the saved conversations. It also identifies the market
generator, prompts, model settings, and experiment runner, so you can follow
an interaction from its setup to its evaluation. Shared API and file-handling
utilities and request construction live in `experiment_runtime/`. Model IDs,
provider routing, and generation defaults for all three experiments live in
[`config/`](config/README.md).
