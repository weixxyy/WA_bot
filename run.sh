#!/usr/bin/env bash
# Compatibility alias. Use start.sh for new installations.
set -euo pipefail
cd "$(dirname "$0")"
exec ./start.sh "$@"
