"""Scripted driver for tests and screenshots: python -m dwinui.sim.harness OUTDIR"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ..config import Config
from ..printer import Printer
from ..ui.app import App
from .fake_moonraker import FakeMoonraker
from .link import SimLink
from .renderer import Canvas

BUILT_ASSETS = Path(__file__).resolve().parents[2] / "build" / "DWIN_SET"


class Harness:
    def __init__(self, cfg: Config | None = None, assets: Path | None = None,
                 db: dict | None = None) -> None:
        self.cfg = cfg or Config(long_press_ms=150, dim_timeout=0)
        self.printer = Printer()
        self.api = FakeMoonraker(self.printer, db=db)
        self.link = SimLink(Canvas(assets))
        self.app = App(self.cfg, self.link, self.api, self.printer)
        self._tasks: list[asyncio.Task] = []

    async def __aenter__(self) -> Harness:
        await self.link.open()
        self._tasks = [asyncio.create_task(c) for c in
                       (self.api.run(), self.app.run(), self.app.services())]
        await self.settle(0.3)
        return self

    async def __aexit__(self, *exc) -> None:
        crashed = [t for t in self._tasks if t.done() and not t.cancelled() and t.exception()]
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if crashed and exc[0] is None:
            # A background task (the render loop, say) died: fail loudly with
            # its own error instead of some later assertion.
            raise crashed[0].exception()

    async def settle(self, seconds: float = 0.15) -> None:
        await asyncio.sleep(seconds)

    async def rotate(self, delta: int = 1) -> None:
        self.link.rotate(delta)
        await self.settle()

    async def click(self) -> None:
        self.link.press()
        self.link.release()
        await self.settle()

    async def back(self) -> None:
        self.link.press()
        await asyncio.sleep(self.cfg.long_press_ms / 1000 + 0.05)
        self.link.release()
        await self.settle()

    def shot(self, path: str | Path) -> None:
        self.link.canvas.snapshot(scale=2).save(path)


async def tour(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    custom = BUILT_ASSETS.exists()
    cfg = Config(long_press_ms=150, dim_timeout=0, custom_assets=custom)
    async with Harness(cfg, BUILT_ASSETS if custom else None) as h:
        h.shot(outdir / "01_status_idle.png")
        await h.click()
        h.shot(outdir / "02_menu.png")
        await h.click()                       # Print
        await h.settle(0.2)
        h.shot(outdir / "03_files.png")
        await h.rotate(2)
        await h.click()                       # first file -> preview
        await h.settle(0.2)
        h.shot(outdir / "04_file_confirm.png")
        await h.back()
        await h.back()
        await h.rotate(1)
        await h.click()                       # Temperature
        await h.click()                       # edit hotend
        await h.rotate(4)
        h.shot(outdir / "05_temp_edit.png")
        await h.click()
        await h.back()
        await h.rotate(1)
        await h.click()                       # Move
        await h.click()                       # Home all
        await h.rotate(4)
        await h.click()                       # Jog X
        await h.rotate(3)
        h.shot(outdir / "06_move_jog.png")
        await h.back()
        await h.back()
        await h.rotate(4)
        await h.click()                       # System
        await h.click()                       # E-stop confirm
        h.shot(outdir / "07_estop_confirm.png")
        await h.back()
        await h.back()
        await h.back()                        # status
        await h.api.call("printer.print.start", filename="benchy.gcode")
        await h.settle(1.5)
        h.shot(outdir / "08_status_printing.png")
        await h.click()
        h.shot(outdir / "09_menu_printing.png")
        await h.rotate(2)
        await h.click()                       # Tune
        h.shot(outdir / "10_tune.png")
        await h.back()
        await h.back()
        await h.api.call("printer.emergency_stop")
        await h.settle(0.3)
        h.shot(outdir / "11_shutdown.png")
        h.app.toast("Must home axis first: 0.000 0.000 0.000 [0.000]", "error")
        await h.settle(0.2)
        h.shot(outdir / "12_toast.png")
    print(f"assets: {'custom' if custom else 'stock'}, bytes sent: {h.link.bytes_written}, "
          f"unknown cmds: {h.link.canvas.unknown}")


if __name__ == "__main__":
    asyncio.run(tour(Path(sys.argv[1] if len(sys.argv) > 1 else "shots")))
