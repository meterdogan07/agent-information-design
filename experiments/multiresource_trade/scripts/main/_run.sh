#!/usr/bin/env bash
# Main 90-market study. Usage: bash scripts/main/_run.sh MODEL [preview|execute]
source "$(dirname "$0")/../_common.sh" "$@"

# Keep the original two output groups and market order.
run_peer extraction_region_grid_full90_20260831 \
  --market-panel extraction_region_grid --resources 2 \
  --conditions rank_only exact_values --horizons 8
run_peer extraction_region_grid_least90_20260901 \
  --market-panel extraction_region_grid --resources 2 \
  --conditions inventory_only --horizons 8
