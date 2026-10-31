"""KlipperScreen-style tile menu. Tiles change with the job state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ... import protocol as p
from .. import theme as t
from ..screen import ButtonWidget, Confirm, HeaderScreen
from ..widgets import Tile

COLS = 2
GAP = 6
BACK_H = 32


@dataclass
class Entry:
    label: str
    action: Callable[[], None]
    sub: Callable[[], str] | None = None
    icon: str = ""


class MenuScreen(HeaderScreen):
    title = "Menu"
    ROWS = 5
    LAYOUT = "row"
    FONT = 1

    def __init__(self, app) -> None:
        super().__init__(app)
        self.selected = 0
        self.entries: list[Entry] = []
        back_y = p.HEIGHT - GAP - BACK_H
        tile_w = (p.WIDTH - GAP * (COLS + 1)) // COLS
        tile_h = (back_y - t.HEADER_H - GAP * (self.ROWS + 1)) // self.ROWS
        self.tiles = []
        for i in range(COLS * self.ROWS):
            row, col = divmod(i, COLS)
            x = GAP + col * (tile_w + GAP)
            y = t.HEADER_H + GAP + row * (tile_h + GAP)
            self.tiles.append(Tile(f"menu.{i}", (x, y, x + tile_w - 1, y + tile_h - 1), self.LAYOUT,
                                   self.FONT))
        self.back = ButtonWidget("menu.back", (GAP, back_y, p.WIDTH - 1 - GAP, back_y + BACK_H - 1),
                                 "← Back", t.ACCENT)
        self.back.idle_bg = t.TILE_BG
        self.back.font = self.FONT
        self.widgets += self.tiles + [self.back]

    def _entries(self) -> list[Entry]:
        from ...printer import FAN_TYPES, LIGHT_TYPES
        from .devices import FansScreen, LightsScreen
        from .extrude import ExtrudeScreen
        from .files import FilesScreen
        from .macros import MacrosScreen
        from .move import MoveScreen
        from .system import SystemScreen
        from .temperature import TemperatureScreen
        from .tune import TuneScreen

        pr = self.printer
        go = lambda cls: lambda: self.app.push(cls(self.app))  # noqa: E731

        def temps() -> str:
            return f"{pr.temp('extruder')[0]:.0f}° {pr.temp('heater_bed')[0]:.0f}°"

        tune = Entry("Tune", go(TuneScreen),
                     lambda: f"{pr.get('gcode_move', 'speed_factor', 1.0) * 100:.0f}%", "tune")
        temp = Entry("Temp", go(TemperatureScreen), temps, "temp")
        devices = []
        if pr.devices(FAN_TYPES):
            devices.append(Entry("Fans", go(FansScreen), lambda: f"{pr.get('fan', 'speed', 0.0) * 100:.0f}%",
                                 "fans"))
        if pr.devices(LIGHT_TYPES):
            devices.append(Entry("Lights", go(LightsScreen), icon="lights"))
        if pr.job_active:
            # Paused is when you'd swap filament; printing is when you'd nudge the head.
            if pr.job_state == "paused":
                pause = Entry("Resume", lambda: self.api.fire("printer.print.resume"), icon="resume")
                tool = Entry("Extrude", go(ExtrudeScreen), icon="extrude")
            else:
                pause = Entry("Pause", lambda: self.api.fire("printer.print.pause"), icon="pause")
                tool = Entry("Move", go(MoveScreen), icon="move")
            return [
                pause,
                Entry("Cancel", self._cancel, icon="cancel"),
                tune, temp, *devices,
                Entry("Macros", go(MacrosScreen), icon="macros"),
                tool,
                Entry("System", go(SystemScreen), icon="system"),
            ]
        return [
            Entry("Print", go(FilesScreen), icon="print"),
            temp,
            Entry("Move", go(MoveScreen), lambda: pr.homed_axes.upper() and f"homed {pr.homed_axes.upper()}",
                  "move"),
            Entry("Extrude", go(ExtrudeScreen), icon="extrude"),
            tune,
            Entry("Macros", go(MacrosScreen), icon="macros"),
            *devices,                                   # Fans and Lights share a row
            Entry("System", go(SystemScreen), icon="system"),
        ]

    def _cancel(self) -> None:
        def ok() -> None:
            self.api.fire("printer.print.cancel")
            self.app.pop_to_root()
        self.app.push(Confirm(self.app, "Cancel print?", self.printer.get("print_stats", "filename", ""),
                              ok, ok="Cancel it", cancel="Keep", danger=True))

    def refresh(self) -> None:
        super().refresh()
        self.entries = self._entries()
        self.selected = min(self.selected, len(self.entries))     # == len: the Back strip
        for i, tile in enumerate(self.tiles):
            entry = self.entries[i] if i < len(self.entries) else None
            tile.label = entry.label if entry else ""
            tile.sub = (entry.sub() or "") if entry and entry.sub else ""
            tile.selected = entry is not None and i == self.selected
            tile.empty = entry is None
            name = entry.icon if entry else ""
            tile.icon = self.app.assets.icon("tile", name)
            tile.icon_sel = self.app.assets.icon("tile_sel", name)

        self.back.selected = self.selected == len(self.entries)

    def on_rotate(self, delta: int) -> None:
        self.selected = max(0, min(len(self.entries), self.selected + delta))

    def on_click(self) -> None:
        if self.selected == len(self.entries):
            self.app.pop()
        elif 0 <= self.selected < len(self.entries):
            self.entries[self.selected].action()
