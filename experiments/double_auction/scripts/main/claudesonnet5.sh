#!/usr/bin/env bash
# claudesonnet5: main auction panel. Pass preview, freeze, or execute (default: preview).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec bash "$ROOT/run.sh" claudesonnet5 margin "${@:-preview}" \
  --markets 30 --seed 97617
