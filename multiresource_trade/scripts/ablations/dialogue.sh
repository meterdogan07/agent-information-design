#!/usr/bin/env bash
# dialogue study. Usage: bash scripts/ablations/dialogue.sh MODEL [preview|execute]
source "$(dirname "$0")/../_common.sh" "$@"

run_peer horizons --market-panel responder_extraction_crossed \
  --market-ids \
    a-high_gap_1_11__b-near_flat_5_7__q2_1__xy \
    a-high_gap_1_11__b-near_flat_4_8__q2_1__yx \
    a-high_gap_2_10__b-near_flat_3_9__q2_1__yx \
    a-high_gap_1_11__b-near_flat_5_7__q3_1__yx \
    a-high_gap_1_11__b-near_flat_4_8__q3_1__xy \
    a-high_gap_2_10__b-near_flat_3_9__q3_1__xy \
  --conditions inventory_only rank_only exact_values --horizons 2 4 6 8 12
