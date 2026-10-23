# Shared model and generation settings

All three experiments use this directory. There is no separate model catalog
inside resource trade.

- `models.json`: model IDs, API providers, pinned serving backends, run tags,
  and budget-accounting prices. GPT-4o mini is an optional testing preset.
- `generation.json`: reasoning defaults, output-token limits, timeouts, and
  bargaining/auction attempt limits. `temperature: null` means the request
  omits temperature and uses the provider default. Study presets require this
  policy; it is not a claim that providers use identical sampling settings.
- `../models.py`: validates and loads these settings.
- `../provider.py`: shared API client, request construction,
  response extraction, and tracing primitives.

Inspect a preset without loading credentials or calling a model:

```bash
python -m experiment_runtime.models gpt51 --study trade
python -m experiment_runtime.models gemini --study bargaining
```

The study adapters retain their response schemas, validation, and retry logic.
They do not import one another. Existing command-line overrides remain available.

## Preserved study differences

Trade launchers and mixed-model runs use 4,096 output tokens; the low-level
trade and mediator runners retain their 2,048-token default (`runner_max_tokens`).
Bargaining uses 4,096 and auctions use 2,048. Explicit run overrides still win.
Reasoning defaults to low, except the optional GPT-4o mini preset uses none.

Budget prices are estimates copied from the existing implementations, not live
provider quotes. Gemini's older single-model bargaining rates remain under
`pricing_by_study`; mixed-model runs retain their existing rates. Verify prices
before authorizing new paid experiments.

Shared code and these JSON files are included in the source fingerprints used
by frozen mixed-model studies and bargaining snapshots. Auction paid-run checks
also require them to be committed. Changing them may require a new frozen plan;
do not overwrite an existing run's configuration to bypass a mismatch.

Existing saved results and their prompt identifiers are not rewritten. New
trade run tags use the model tag plus `_engineids`; the prompt has no revision
nickname. The prompt text itself has not changed.
