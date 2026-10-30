"""KlipperScreen-style tile menu. Tiles change with the job state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ... import protocol as p
from .. import theme as t
from ..screen import Confirm, HeaderScreen
from ..widgets import Tile

COLS, ROWS = 2, 4
GAP = 8


@dataclass
class Entry:
    label: str
    action: Callable[[], None]
    sub: Callable[[], str] | None = None


class MenuScreen(HeaderScreen):
    title = "Menu"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.selected = 0
        self.entries: list[Entry] = []
        tile_w = (p.WIDTH - GAP * (COLS + 1)) // COLS
        tile_h = (p.HEIGHT - t.HEADER_H - 48 - GAP * (ROWS + 1)) // ROWS
        self.tiles = []
        for i in range(COLS * ROWS):
            row, col = divmod(i, COLS)
            x = GAP + col * (tile_w + GAP)
            y = t.HEADER_H + GAP + row * (tile_h + GAP)
            self.tiles.append(Tile(f"menu.{i}", (x, y, x + tile_w - 1, y + tile_h - 1)))
        self.widgets += self.tiles

    def _entries(self) -> list[Entry]:
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
            e, et = pr.temp("extruder")
            b, bt = pr.temp("heater_bed")
            return f"{e:.0f}/{et:.0f} {b:.0f}/{bt:.0f}"

        tune = Entry("Tune", go(TuneScreen),
                     lambda: f"{pr.get('gcode_move', 'speed_factor', 1.0) * 100:.0f}% speed")
        temp = Entry("Temp", go(TemperatureScreen), temps)
        if pr.job_active:
            # Paused is when you'd swap filament; printing is when you'd nudge the head.
            if pr.job_state == "paused":
                pause = Entry("Resume", lambda: self.api.fire("printer.print.resume"))
                tool = Entry("Extrude", go(ExtrudeScreen))
            else:
                pause = Entry("Pause", lambda: self.api.fire("printer.print.pause"))
                tool = Entry("Move", go(MoveScreen))
            return [
                pause,
                Entry("Cancel", self._cancel),
                tune, temp,
                Entry("Macros", go(MacrosScreen)),
                tool,
                Entry("System", go(SystemScreen)),
                Entry("Back", self.app.pop),
            ]
        return [
            Entry("Files", go(FilesScreen)),
            temp,
            Entry("Move", go(MoveScreen), lambda: pr.homed_axes.upper() and f"homed {pr.homed_axes.upper()}"),
            Entry("Extrude", go(ExtrudeScreen)),
            tune,
            Entry("Macros", go(MacrosScreen)),
            Entry("System", go(SystemScreen)),
            Entry("Back", self.app.pop),
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
        self.selected = min(self.selected, len(self.entries) - 1)
        for i, tile in enumerate(self.tiles):
            entry = self.entries[i] if i < len(self.entries) else None
            tile.label = entry.label if entry else ""
            tile.sub = (entry.sub() or "") if entry and entry.sub else ""
            tile.selected = i == self.selected

    def on_rotate(self, delta: int) -> None:
        self.selected = max(0, min(len(self.entries) - 1, self.selected + delta))

    def on_click(self) -> None:
        if 0 <= self.selected < len(self.entries):
            self.entries[self.selected].action()
