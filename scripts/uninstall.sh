#!/usr/bin/env bash
# Remove user-local links and desktop entry. Config, cache and ledger remain.
set -euo pipefail
rm -f "$HOME/.local/bin/qa-release"
rm -f "$HOME/.local/share/applications/qa-release-board.desktop"
echo "removed CLI link and desktop entry"
echo "kept: ~/.config/qa-release-board and release/"
