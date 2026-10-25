#!/usr/bin/env bash
# mediator study. Usage: bash scripts/ablations/mediator.sh MODEL [preview|execute]
source "$(dirname "$0")/../_common.sh" "$@"

python -m src.trading.run_mediator "${MODEL_ARGS[@]}" \
  --mediator-mode one_shot --max-tokens "$GENERATION_MAX_TOKENS" --workers "$WORKERS" \
  --market-panel responder_extraction_crossed \
  --conditions inventory_only rank_only exact_values \
  --replications "${REPLICATIONS:-1}" \
  --results-dir "$RESULT_BASE/$MODEL_TAG/one_shot_mediator" \
  ${RUN_FLAG:+$RUN_FLAG}
