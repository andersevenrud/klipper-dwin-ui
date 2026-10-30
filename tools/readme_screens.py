#!/usr/bin/env python3
"""Render the README screenshots from the simulator, with the custom asset
pack (run tools/build_assets.py first).

    python tools/readme_screens.py --preview ws://printer:7125/websocket 3DBenchy.gcode
                                                      # -> docs/img/print-preview.png
    python tools/readme_screens.py                    # -> docs/img/screens.png

The strip shows home, menu, print preview and printing. Everything comes
from the fake printer except the print preview, which is taken from
docs/img/print-preview.png when it exists (a real slicer thumbnail looks
better than the fake one). --preview only reads from the printer: file
metadata and the thumbnail.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dwinui.config import Config  # noqa: E402
from dwinui.moonraker import Moonraker  # noqa: E402
from dwinui.printer import Printer  # noqa: E402
from dwinui.sim.harness import BUILT_ASSETS, Harness  # noqa: E402
from dwinui.sim.link import SimLink  # noqa: E402
from dwinui.sim.renderer import Canvas  # noqa: E402
from dwinui.ui.app import App  # noqa: E402
from dwinui.ui.screens.files import PrintPreview  # noqa: E402

GAP = 24
SCALE = 2


PREVIEW = ROOT / "docs" / "img" / "print-preview.png"


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
        if PREVIEW.exists():
            shots.append(Image.open(PREVIEW).convert("RGB"))
        else:
            meta = await h.api.call("server.files.metadata", filename="benchy.gcode")
            h.app.push(PrintPreview(h.app, "benchy.gcode", meta))
            await h.settle(0.5)
            shots.append(h.link.canvas.snapshot(SCALE))      # print preview (fake thumbnail)
            await h.back()
        await h.back()
        await h.api.call("printer.print.start", filename="3DBenchy_PLA_31m20s.gcode")
        await h.settle(12)                                   # let the demo print progress
        shots.append(h.link.canvas.snapshot(SCALE))          # printing
    return shots


async def capture_preview(url: str, filename: str) -> Image.Image:
    cfg = Config(dim_timeout=0, custom_assets=True)
    printer = Printer()
    api = Moonraker(printer, url)
    link = SimLink(Canvas(BUILT_ASSETS))
    await link.open()
    app = App(cfg, link, api, printer)
    tasks = [asyncio.create_task(c) for c in (api.run(), app.run(), app.services())]
    try:
        for _ in range(50):
            await asyncio.sleep(0.2)
            if printer.ready:
                break
        meta = await api.call("server.files.metadata", filename=filename)
        app.push(PrintPreview(app, filename, meta))
        await asyncio.sleep(2)
        if not app.top.thumb.jpeg:
            raise SystemExit(f"{filename} has no usable thumbnail")
        return link.canvas.snapshot(SCALE)
    finally:
        for task in tasks:
            task.cancel()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preview", nargs=2, metavar=("URL", "FILE"),
                    help="render the print preview of FILE from the printer at URL")
    args = ap.parse_args()
    if args.preview:
        img = asyncio.run(capture_preview(*args.preview))
        img.quantize(colors=128, method=Image.Quantize.FASTOCTREE).save(PREVIEW, optimize=True)
        print(f"-> {PREVIEW.relative_to(ROOT)} ({PREVIEW.stat().st_size // 1024} KB)")
        return
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
