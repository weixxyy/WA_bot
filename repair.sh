#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export WA_BOT_FORCE_REPAIR=1
exec ./setup.sh
