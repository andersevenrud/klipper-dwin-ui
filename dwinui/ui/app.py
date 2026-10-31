"""Screen stack, input dispatch, toasts, idle dimming and the render loop."""

from __future__ import annotations

import asyncio
import logging
import time

from .. import protocol as p
from ..assets import Assets
from ..config import Config
from ..display import Display, intersects
from ..history import TempHistory
from ..link import LinkClosed
from ..settings import Settings
from ..link import Link
from ..printer import Printer
from . import theme as t
from .screen import Screen
from .widgets import wrap

log = logging.getLogger(__name__)

TOAST_COLORS = {"info": t.HEADER_BG, "ok": t.OK, "error": t.ERROR}

# The panel can reboot on its own (a power glitch) while the serial link
# stays up, and then shows its boot screen while we think the UI is still
# there. It answers the handshake within ~0.4 s when alive, so ping it and
# redraw everything when it comes back from a silence. LOST_AFTER leaves
# room for a large flush (a thumbnail upload) delaying the reply.
HEARTBEAT = 0.5
LOST_AFTER = 2.0


class App:
    def __init__(self, cfg: Config, link: Link, api, printer: Printer) -> None:
        from .screens.dashboard import DashboardScreen

        self.cfg = cfg
        self.link = link
        self.api = api
        self.printer = printer
        self.assets = Assets.load() if cfg.custom_assets else Assets()
        p.set_glyphs(self.assets.glyphs)
        self.display = Display(link, t.BG)
        self.history = TempHistory()
        self.settings = Settings()
        self.settings.listen(self._apply_settings)
        self.stack: list[Screen] = [DashboardScreen(self)]
        self._job_was_active = False
        self._job_seen = False          # a job ran while we watched
        self._job_dismissed = False
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
        self._last_reply = time.monotonic()
        self.panel_lost = False

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

    def redraw(self) -> None:
        """Clear and redraw everything on the next render."""
        self._full = True
        self.invalidate()

    def _sync_root(self) -> None:
        """Dashboard normally; the printing screen while a job runs and after
        it ends, until dismissed."""
        from .screens.dashboard import DashboardScreen
        from .screens.printing import PrintingScreen

        pr = self.printer
        active = pr.job_active
        if active and not self._job_was_active:
            self._job_seen = True
            self._job_dismissed = False
            self.pop_to_root()          # a new job: show it, wherever it was started from
        self._job_was_active = active
        finished = pr.job_state in ("complete", "cancelled", "error")
        want = PrintingScreen if active or (self._job_seen and finished and not self._job_dismissed) \
            else DashboardScreen
        if not isinstance(self.stack[0], want):
            self.stack[0] = want(self)
            if len(self.stack) == 1:
                self._full = True

    def dismiss_job(self) -> None:
        self._job_dismissed = True
        self.invalidate()

    async def services(self) -> None:
        """Background work that outlives display reconnects."""
        await asyncio.gather(self.history.run(self.printer, self.api, self.invalidate),
                             self.settings.run(self.printer, self.api))

    def _apply_settings(self) -> None:
        """Saved settings arrived (or were reset): use them, config as fallback."""
        level = int(self.settings.get("brightness", self.cfg.brightness))
        if level != self.brightness:
            self.brightness = level
            if not self.dimmed and not self.link.closed.is_set():
                asyncio.ensure_future(self._set_backlight(level))
        self.invalidate()

    async def _set_backlight(self, level: int) -> None:
        try:
            await self.display.set_brightness(level)
        except LinkClosed:
            pass

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
        if self.printer.ready:
            self._sync_root()
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
            drew = False
            for key, area, frames in widget.parts():
                if self.display.draw(key, area, frames):
                    drew = True
                    under_toast = under_toast or intersects(area, t.TOAST_RECT)
            incremental = getattr(widget, "incremental", None)
            if incremental:
                self.display.send(incremental(drew))

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

    async def frames_loop(self) -> None:
        """Replies from the panel. Only handshake answers matter."""
        while True:
            payload = await self.link.frames.get()
            if not p.is_handshake_reply(payload):
                continue
            self._last_reply = time.monotonic()
            if self.panel_lost:
                self.panel_lost = False
                log.warning("display answers again; redrawing")
                await self.display.init(self.display.brightness_level)
                self._full = True
                self.invalidate()

    async def heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT)
            await self.link.write(p.handshake())
            if not self.panel_lost and time.monotonic() - self._last_reply > LOST_AFTER:
                self.panel_lost = True
                log.warning("display stopped answering (power glitch?)")

    async def set_brightness(self, level: int) -> None:
        """Change the backlight from the display, and remember it."""
        self.brightness = level
        self.settings.set(self.api, "brightness", level)
        await self.display.set_brightness(level)

    async def run(self) -> None:
        """Run the UI until the link closes (caller reconnects)."""
        self._full = True
        await self.display.handshake()
        await self.display.init(self.brightness)
        self.dimmed = False
        self.last_input = time.monotonic()
        self._last_reply = time.monotonic()
        self.panel_lost = False
        tasks = [asyncio.create_task(c) for c in
                 (self.render_loop(), self.input_loop(), self.idle_loop(), self.frames_loop(),
                  self.heartbeat_loop(), self.link.closed.wait())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in tasks:
                if task.done() and not task.cancelled() and task.exception():
                    raise task.exception()
        finally:
            for task in tasks:
                task.cancel()
