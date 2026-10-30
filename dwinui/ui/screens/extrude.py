"""Manual extrude/retract."""

from __future__ import annotations

from ..screen import Item, ListScreen, NumberEdit
from .common import heater_item


class ExtrudeScreen(ListScreen):
    title = "Extrude"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.length = 10.0
        self.speed = self.cfg.extrude_feedrate / 60
        self.set_items([
            heater_item(self, "extruder"),
            Item("Length", value=lambda: f"{self.length:g} mm",
                 edit=NumberEdit(lambda: self.length, self._set_length, 1, 200, step=1)),
            Item("Speed", value=lambda: f"{self.speed:g} mm/s",
                 edit=NumberEdit(lambda: self.speed, self._set_speed, 1, 30, step=1)),
            Item("Extrude", action=lambda: self._move(self.length)),
            Item("Retract", action=lambda: self._move(-self.length)),
        ])

    def _set_length(self, v: float) -> None:
        self.length = v

    def _set_speed(self, v: float) -> None:
        self.speed = v

    def _move(self, distance: float) -> None:
        temp, _ = self.printer.temp("extruder")
        if temp < self.cfg.min_extrude_temp:
            self.app.toast(f"Hotend below {self.cfg.min_extrude_temp:.0f}C", "error")
            return
        self.api.gcode_fire(
            "SAVE_GCODE_STATE NAME=_dwinui_extrude\nM83\n"
            f"G1 E{distance:.2f} F{self.speed * 60:.0f}\n"
            "RESTORE_GCODE_STATE NAME=_dwinui_extrude")
        self.app.toast(("Extruding" if distance > 0 else "Retracting") + f" {abs(distance):g} mm")
