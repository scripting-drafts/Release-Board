#!/usr/bin/env bash
# Run the current Release Board build.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$ROOT/node_modules/.bin/electron" "$ROOT" "$@"
