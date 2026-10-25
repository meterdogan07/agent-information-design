#!/usr/bin/env bash
# grok43: main trade panel. Preview by default; use execute for paid calls.
set -euo pipefail
exec bash "$(dirname "$0")/_run.sh" grok43 "${@:-preview}"
