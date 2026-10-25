#!/usr/bin/env bash
# resources study. Usage: bash scripts/ablations/resources.sh MODEL [preview|execute]
source "$(dirname "$0")/../_common.sh" "$@"

for count in 2 6 10; do
  run_peer "heterogeneous_resources_$count" \
    --market-panel heterogeneous_complexity_scaling --resources "$count" \
    --conditions inventory_only rank_only exact_values --horizons 8
done
