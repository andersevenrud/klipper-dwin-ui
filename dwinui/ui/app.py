"""Screen stack, input dispatch, toasts, idle dimming and the render loop."""

from __future__ import annotations

import asyncio
import logging
import time

from .. import protocol as p
from ..assets import Assets
from ..config import Config
from ..display import Display, intersects
from ..link import Link
from ..printer import Printer
from . import theme as t
from .screen import Screen
from .widgets import wrap

log = logging.getLogger(__name__)

TOAST_COLORS = {"info": t.HEADER_BG, "ok": t.OK, "error": t.ERROR}


class App:
    def __init__(self, cfg: Config, link: Link, api, printer: Printer) -> None:
        from .screens.status import StatusScreen

        self.cfg = cfg
        self.link = link
        self.api = api
        self.printer = printer
        self.assets = Assets.load() if cfg.custom_assets else Assets()
        p.set_glyphs(self.assets.glyphs)
        self.display = Display(link, t.BG)
        self.stack: list[Screen] = [StatusScreen(self)]
        self.blocker: Screen | None = None
        self._wake = asyncio.Event()
        self._full = True
        self._toast: tuple[str, str, float] | None = None
        self._toast_drawn = False
        self._long_task: asyncio.Task | None = None
        self._long_fired = False
        self._swallow_release = False
        self.last_input = time.monotonic()
        self.dimmed = False
        self.brightness = cfg.brightness

        printer.listen(self.invalidate)
        api.on_error = lambda msg: self.toast(msg, "error")
        api.on_gcode_response = self._gcode_response

    # -- navigation -------------------------------------------------------

    @property
    def top(self) -> Screen:
        return self.blocker or self.stack[-1]

    def push(self, screen: Screen) -> None:
        self.stack.append(screen)
        if not screen.modal:
            self._full = True
        screen.on_enter()
        self.invalidate()

    def pop(self) -> None:
        if len(self.stack) <= 1:
            return
        screen = self.stack.pop()
        screen.on_exit()
        self._closed(screen)
        self.invalidate()

    def pop_to_root(self) -> None:
        while len(self.stack) > 1:
            self.pop()

    def replace(self, screen: Screen) -> None:
        old = self.stack.pop()
        old.on_exit()
        self._closed(old)
        self.push(screen)

    def _closed(self, screen: Screen) -> None:
        if screen.modal and not self._full:
            self.display.invalidate(screen.area)
            for key in self.display.cached_keys():
                if key.startswith("dlg."):
                    self.display.forget(key)
            self._toast_drawn = False
        else:
            self._full = True

    def _sync_blocker(self) -> None:
        from .screens.blocker import BlockerScreen

        if not self.printer.ready and self.blocker is None:
            self.blocker = BlockerScreen(self)
            self._full = True
        elif self.printer.ready and self.blocker is not None:
            self.blocker = None
            self.pop_to_root()
            self._full = True

    # -- feedback ---------------------------------------------------------

    def toast(self, message: str, level: str = "info", seconds: float = 3.0) -> None:
        log.info("toast[%s]: %s", level, message)
        self._toast = (message, level, time.monotonic() + seconds)
        self._toast_drawn = False
        if level == "error":
            asyncio.ensure_future(self.beep(120))
        self.invalidate()

    def _gcode_response(self, line: str) -> None:
        if line.startswith("!!"):
            self.toast(line.lstrip("! ").strip(), "error", 5.0)

    async def beep(self, ms: int | None = None) -> None:
        try:
            await self.link.beep(ms or self.cfg.beep_ms)
        except Exception:  # noqa: BLE001 - a missed beep is harmless
            pass

    def invalidate(self) -> None:
        self._wake.set()

    # -- input ------------------------------------------------------------

    def handle_input(self, ev: p.InputEvent) -> None:
        self.last_input = time.monotonic()
        if self.dimmed:
            self.dimmed = False
            asyncio.ensure_future(self.display.set_brightness(self.brightness))
            if isinstance(ev, p.Button) and ev.pressed:
                self._swallow_release = True
            return
        if isinstance(ev, p.Rotate):
            self.top.on_rotate(-ev.delta if self.cfg.invert_encoder else ev.delta)
        elif ev.pressed:
            self._long_fired = False
            if self._long_task:
                self._long_task.cancel()
            self._long_task = asyncio.ensure_future(self._long_press())
        else:
            if self._swallow_release:
                self._swallow_release = False
                return
            if self._long_task and not self._long_task.done():
                self._long_task.cancel()
            if not self._long_fired:
                if self.cfg.beep_on_click:
                    asyncio.ensure_future(self.beep())
                self.top.on_click()
        self.invalidate()

    async def _long_press(self) -> None:
        await asyncio.sleep(self.cfg.long_press_ms / 1000)
        self._long_fired = True
        self.top.on_back()
        self.invalidate()

    # -- rendering ----------------------------------------------------------

    def render(self) -> None:
        self._sync_blocker()
        now = time.monotonic()
        if self._full:
            self.display.clear(t.BG)
            self._full = False
            self._toast_drawn = False
        if self._toast and now >= self._toast[2]:
            self._toast = None
            self.display.invalidate(t.TOAST_RECT)
            self.display.forget("toast")

        screen = self.top
        screen.refresh()
        under_toast = False
        for widget in screen.widgets:
            for key, area, frames in widget.parts():
                if self.display.draw(key, area, frames) and intersects(area, t.TOAST_RECT):
                    under_toast = True

        if self._toast:
            if under_toast or not self._toast_drawn:
                self.display.forget("toast")
            msg, level, _ = self._toast
            x1, y1, x2, y2 = t.TOAST_RECT
            bg = TOAST_COLORS.get(level, t.HEADER_BG)
            frames = [p.fill(bg, *t.TOAST_RECT)]
            lines = wrap(msg, 32, 2)
            ly = y1 + (12 if len(lines) == 1 else 4)
            for i, line in enumerate(lines):
                frames.append(p.text(x1 + 8, ly + i * 20, line, 1, t.FG, bg))
            self.display.draw("toast", t.TOAST_RECT, frames)
            self._toast_drawn = True

    async def render_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), 1.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            self.render()
            await self.display.flush()
            await asyncio.sleep(0.04)   # coalesce bursts of updates

    async def input_loop(self) -> None:
        while True:
            ev = await self.link.inputs.get()
            self.handle_input(ev)

    async def idle_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            timeout = self.cfg.dim_timeout
            if timeout and not self.dimmed and time.monotonic() - self.last_input > timeout:
                self.dimmed = True
                await self.display.set_brightness(self.cfg.dim_brightness)

    async def set_brightness(self, level: int) -> None:
        self.brightness = level
        await self.display.set_brightness(level)

    async def run(self) -> None:
        """Run the UI until the link closes (caller reconnects)."""
        self._full = True
        await self.display.handshake()
        await self.display.init(self.brightness)
        self.dimmed = False
        self.last_input = time.monotonic()
        tasks = [asyncio.create_task(c) for c in
                 (self.render_loop(), self.input_loop(), self.idle_loop(), self.link.closed.wait())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in tasks:
                if task.done() and not task.cancelled() and task.exception():
                    raise task.exception()
        finally:
            for task in tasks:
                task.cancel()
