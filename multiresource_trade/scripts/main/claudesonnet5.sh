#!/usr/bin/env bash
# claudesonnet5: main trade panel. Preview by default; use execute for paid calls.
set -euo pipefail
exec bash "$(dirname "$0")/_run.sh" claudesonnet5 "${@:-preview}"
