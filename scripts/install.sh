#!/usr/bin/env bash
# User-local install: CLI link, Zed tasks and desktop entry.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"

if [[ ! -x "$ROOT/node_modules/.bin/electron" ]]; then
  npm --prefix "$ROOT" install --no-audit --no-fund
fi

mkdir -p "$BIN" "$APPS"
ln -sfn "$ROOT/bin/qa-release" "$BIN/qa-release"
"$ROOT/bin/qa-release" zed-tasks

cat > "$APPS/qa-release-board.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=QA Release Board
Comment=Standalone Debian and Launchpad release cockpit
Exec="$ROOT/node_modules/.bin/electron" "$ROOT"
Icon=$ROOT/assets/qa-release-board.svg
Terminal=false
Categories=Development;
StartupNotify=true
EOF
chmod 0644 "$APPS/qa-release-board.desktop"

echo "installed: $BIN/qa-release"
echo "installed: $APPS/qa-release-board.desktop"
echo "installed: $HOME/.config/zed/tasks.json"
