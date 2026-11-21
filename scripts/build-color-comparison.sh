#!/usr/bin/env bash
# Generate first-vs-current color comparison screenshots.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/dist/color-comparison}"
mkdir -p "$OUT"

capture() {
  local variant="$1" view="$2" name="$3"
  timeout 45 "$ROOT/node_modules/.bin/electron" "$ROOT" \
    --colors="$variant" --view="$view" --ozone-platform=x11 \
    --screenshot="$OUT/${name}-${variant}.png"
}

for view in overview tasks compare settings; do
  capture first "$view" "$view"
  capture current "$view" "$view"
done

python3 - "$OUT" <<'PY'
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

out = Path(sys.argv[1])
font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
font = ImageFont.truetype(str(font_path), 28) if font_path.exists() else ImageFont.load_default()

for view in ("overview", "tasks", "compare", "settings"):
    left_path = out / f"{view}-first.png"
    right_path = out / f"{view}-current.png"
    if not left_path.exists() or not right_path.exists():
        continue
    left = Image.open(left_path).convert("RGB")
    right = Image.open(right_path).convert("RGB")
    width = max(left.width, right.width)
    height = max(left.height, right.height)
    label_h = 48
    sheet = Image.new("RGB", (width * 2 + 24, height + label_h), "#ffffff")
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 10), "First color scheme", fill="#101c2c", font=font)
    draw.text((width + 36, 10), "Current", fill="#101c2c", font=font)
    sheet.paste(left, (0, label_h))
    sheet.paste(right, (width + 24, label_h))
    target = out / f"{view}-comparison.png"
    sheet.save(target)
    print(target)
PY
