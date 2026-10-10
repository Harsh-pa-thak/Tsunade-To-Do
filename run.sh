#!/usr/bin/env bash
# Launch the Tsunade desktop pet from anywhere.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
exec python3 -m tsunade_pet "$@"
