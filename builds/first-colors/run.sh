#!/usr/bin/env bash
# Run the current app with the first Release Board component colors.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$ROOT/node_modules/.bin/electron" "$ROOT" --colors=first "$@"
