"""Shown instead of the normal UI while Moonraker or Klippy isn't ready."""

from __future__ import annotations

from ... import protocol as p
from .. import theme as t
from ..screen import Item, ListScreen
from ..widgets import Text, wrap

TITLES = {
    "disconnected": ("Disconnected", t.WARN),
    "startup": ("Starting...", t.FG),
    "shutdown": ("Shutdown", t.ERROR),
    "error": ("Klipper error", t.ERROR),
}
MSG_LINES = 8


class BlockerScreen(ListScreen):
    title = "Klipper"
    back_row = False   # nothing to go back to until Klipper is ready

    def __init__(self, app) -> None:
        super().__init__(app)
        self.list.area = (0, 260, p.WIDTH - 1, p.HEIGHT - 50)
        self.lines = [Text(f"blk.l{i}", 8, 50 + i * 24, 32, font=1, fg=t.DIM)
                      for i in range(MSG_LINES)]
        self.widgets += self.lines
        self.set_items([
            Item("Firmware restart", action=lambda: self.api.fire("printer.firmware_restart"),
                 enabled=self._can_restart),
            Item("Restart Klipper", action=lambda: self.api.fire("printer.restart"),
                 enabled=self._can_restart),
        ])

    def _can_restart(self) -> bool:
        return self.printer.connected and self.printer.klippy_state in ("shutdown", "error")

    def refresh(self) -> None:
        super().refresh()
        pr = self.printer
        state = pr.klippy_state if pr.connected else "disconnected"
        title, color = TITLES.get(state, (state.title(), t.FG))
        if not pr.connected:
            title = "No Moonraker"
        self.header_title.value = title
        self.header_title.fg = color
        message = pr.state_message or ("Waiting for Moonraker..." if not pr.connected else "")
        lines = wrap(message, 32, MSG_LINES)
        for i, widget in enumerate(self.lines):
            widget.value = lines[i] if i < len(lines) else ""
        visible = self._can_restart()
        self.list.rows = self.list.rows if visible else []

    def on_back(self) -> None:
        if self.editing:
            self.editing = None
