#!/usr/bin/env bash
# gpt5mini: preview by default. Use --messages N; execute --budget USD for paid calls.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec bash "$ROOT/run.sh" gpt5mini "$@"
