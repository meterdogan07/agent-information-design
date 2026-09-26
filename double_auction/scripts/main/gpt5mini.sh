#!/usr/bin/env bash
# gpt5mini: main auction panel. Pass preview, freeze, or execute (default: preview).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec bash "$ROOT/run.sh" gpt5mini margin "${@:-preview}" \
  --markets 30 --seed 97617
