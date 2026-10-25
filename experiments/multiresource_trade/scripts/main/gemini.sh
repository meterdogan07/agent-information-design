#!/usr/bin/env bash
# gemini: main trade panel. Preview by default; use execute for paid calls.
set -euo pipefail
exec bash "$(dirname "$0")/_run.sh" gemini "${@:-preview}"
