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
ln -sfn "$ROOT/bin/release-board" "$BIN/release-board"
"$ROOT/bin/release-board" zed-tasks

ICON_DIR="$HOME/.local/share/release-board"
mkdir -p "$ICON_DIR"
cp "$ROOT/assets/release-board.png" "$ICON_DIR/release-board-dark.png"
cp "$ROOT/assets/release-board-light.png" "$ICON_DIR/release-board-light.png"
if gsettings get org.gnome.desktop.interface color-scheme 2>/dev/null | grep -q dark; then
  ICON_TARGET="$ICON_DIR/release-board-dark.png"
else
  ICON_TARGET="$ICON_DIR/release-board-light.png"
fi
ln -sfn "$ICON_TARGET" "$ICON_DIR/icon.png"
rm -f "$ICON_DIR/icon.svg"

cat > "$APPS/release-board.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Release Board
Comment=Standalone Debian and Launchpad release cockpit
Exec="$ROOT/node_modules/.bin/electron" "$ROOT"
Icon=$ICON_DIR/icon.png
Terminal=false
Categories=Development;
StartupNotify=true
EOF
chmod 0644 "$APPS/release-board.desktop"

echo "installed: $BIN/release-board"
echo "installed: $APPS/release-board.desktop"
echo "installed: $HOME/.config/zed/tasks.json"
