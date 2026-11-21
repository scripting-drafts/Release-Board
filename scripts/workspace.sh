#!/usr/bin/env bash
# Open the configured roots as separate projects in one Zed window.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/bin/qa-release" workspace "$@"
