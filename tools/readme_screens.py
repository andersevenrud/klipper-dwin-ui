#!/usr/bin/env python3
"""Render the README screenshot strip from the simulator (fake printer,
custom asset pack). Run tools/build_assets.py first.

    python tools/readme_screens.py  # -> docs/img/screens.png
"""

import asyncio
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dwinui.config import Config  # noqa: E402
from dwinui.sim.harness import BUILT_ASSETS, Harness  # noqa: E402

GAP = 24
SCALE = 2


async def capture() -> list[Image.Image]:
    if not BUILT_ASSETS.exists():
        raise SystemExit("build the asset pack first: python tools/build_assets.py")
    shots = []
    cfg = Config(long_press_ms=150, dim_timeout=0, custom_assets=True)
    async with Harness(cfg, BUILT_ASSETS) as h:
        await h.settle(0.6)
        shots.append(h.link.canvas.snapshot(SCALE))          # home
        await h.click()
        await h.rotate(1)
        shots.append(h.link.canvas.snapshot(SCALE))          # menu, Temp selected
        await h.back()
        await h.api.call("printer.print.start", filename="benchy.gcode")
        await h.settle(12)                                   # let the demo print progress
        shots.append(h.link.canvas.snapshot(SCALE))          # printing
    return shots


def main() -> None:
    shots = asyncio.run(capture())
    w, h = shots[0].size
    strip = Image.new("RGB", (len(shots) * w + (len(shots) - 1) * GAP * SCALE, h), (255, 255, 255))
    for i, img in enumerate(shots):
        strip.paste(img, (i * (w + GAP * SCALE), 0))
    # Transparent gutters so the strip sits well on light and dark pages.
    rgba = strip.convert("RGBA")
    px = rgba.load()
    for i in range(1, len(shots)):
        x0 = i * (w + GAP * SCALE) - GAP * SCALE
        for x in range(x0, x0 + GAP * SCALE):
            for y in range(h):
                px[x, y] = (0, 0, 0, 0)
    out = ROOT / "docs" / "img" / "screens.png"
    rgba.quantize(colors=128, method=Image.Quantize.FASTOCTREE).save(out, optimize=True)
    print(f"-> {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
