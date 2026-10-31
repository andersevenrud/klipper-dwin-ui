"""Read-only console: Moonraker's G-code history, then live responses.

Errors (!!) are red, info (//) grey, commands prefixed with "> ". The knob
scrolls; at the bottom the view follows new messages, scrolled up it stays
put. Press or hold to leave.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator

from ... import protocol as p
from .. import theme as t
from ..screen import HeaderScreen
from ..widgets import Part, ScrollBar, Widget, fit, wrap

log = logging.getLogger(__name__)

HISTORY = 100               # entries fetched from Moonraker's gcode store
MAX_LINES = 400             # wrapped lines kept
ROW_H = 18
CHARS = (p.WIDTH - 16) // p.FONTS[1][0]
# dwinui wraps its own moves in these; they are packaging, not information.
NOISE = ("SAVE_GCODE_STATE NAME=_dwinui", "RESTORE_GCODE_STATE NAME=_dwinui")


def console_lines(kind: str, message: str) -> list[tuple[str, int]]:
    """One store entry -> wrapped (text, colour) lines."""
    out = []
    for raw in message.splitlines() or [""]:
        raw = raw.rstrip()
        if not raw or raw.startswith(NOISE):
            continue
        if kind == "command":
            text, color = "> " + raw, t.FG
        elif raw.startswith("!!"):
            text, color = raw, t.ERROR
        elif raw.startswith("//"):
            text, color = raw, t.DIM
        else:
            text, color = raw, t.FG
        out += [(line, color) for line in wrap(text, CHARS, 50)]
    return out


class LogView(Widget):
    def __init__(self, key: str, area: tuple[int, int, int, int]) -> None:
        self.key, self.area = key, area
        self.lines: list[tuple[str, int]] = []
        self.top = 0

    @property
    def visible(self) -> int:
        return (self.area[3] - self.area[1] + 1) // ROW_H

    @property
    def at_bottom(self) -> bool:
        return self.top >= max(0, len(self.lines) - self.visible)

    def to_bottom(self) -> None:
        self.top = max(0, len(self.lines) - self.visible)

    def parts(self) -> Iterator[Part]:
        x1, y1, x2, _ = self.area
        for i in range(self.visible):
            y = y1 + i * ROW_H
            text, color = self.lines[self.top + i] if self.top + i < len(self.lines) else ("", t.FG)
            yield f"{self.key}.{i}", (x1, y, x2, y + ROW_H - 1), [
                p.text(x1, y + 1, fit(text, CHARS), 1, color, t.BG)]


class ConsoleScreen(HeaderScreen):
    title = "Console"

    def __init__(self, app) -> None:
        super().__init__(app)
        top = t.HEADER_H + 6
        bottom = p.HEIGHT - 8
        self.log = LogView("con.log", (8, top, 8 + CHARS * p.FONTS[1][0] - 1, bottom))
        self.scrollbar = ScrollBar("con.sb", (p.WIDTH - 5, top, p.WIDTH - 3, bottom))
        self.widgets += [self.log, self.scrollbar]
        self.log.lines = [("Loading...", t.DIM)]

    def on_enter(self) -> None:
        self.app.listen_console(self._live)
        asyncio.ensure_future(self._load())

    def on_exit(self) -> None:
        self.app.unlisten_console(self._live)

    async def _load(self) -> None:
        try:
            res = await self.api.call("server.gcode_store", count=HISTORY)
            entries = res.get("gcode_store", [])
        except Exception as e:  # noqa: BLE001
            log.info("no gcode store: %s", e)
            entries = []
        lines = [line for e in entries for line in console_lines(e.get("type", ""), e.get("message", ""))]
        self.log.lines = (lines or [("No messages yet", t.DIM)])[-MAX_LINES:]
        self.log.to_bottom()
        self.app.invalidate()

    def _live(self, kind: str, message: str) -> None:
        follow = self.log.at_bottom
        self.log.lines = (self.log.lines + console_lines(kind, message))[-MAX_LINES:]
        if follow:
            self.log.to_bottom()
        self.app.invalidate()

    def refresh(self) -> None:
        super().refresh()
        self.scrollbar.total = len(self.log.lines)
        self.scrollbar.visible = self.log.visible
        self.scrollbar.top = self.log.top

    def on_rotate(self, delta: int) -> None:
        most = max(0, len(self.log.lines) - self.log.visible)
        self.log.top = max(0, min(most, self.log.top + delta))

    def on_click(self) -> None:
        self.app.pop()
