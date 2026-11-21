#!/usr/bin/env bash
# Install the lifecycle tasks in the global Zed task list.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/bin/qa-release" zed-tasks "$@"
