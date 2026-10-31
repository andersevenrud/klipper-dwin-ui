"""Filament: heat to a preset, the user's load/unload macros, and manual
extrude/retract. dwinui has no load/unload routine of its own."""

from __future__ import annotations

from ..screen import Item, ListScreen, NumberEdit
from .common import heater_item, set_target


class ExtrudeScreen(ListScreen):
    title = "Extrude"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.length = float(self.app.settings.get("extrude_length", 10.0))
        self.speed = float(self.app.settings.get("extrude_speed", self.cfg.extrude_feedrate / 60))
        items = [heater_item(self, "extruder")]
        if self.cfg.presets:
            items.append(Item("Heat", value=lambda: "preset", action=self._heat))
        for label, name in (("Load", self.cfg.load_macro), ("Unload", self.cfg.unload_macro)):
            macro = self.printer.macro(name) if name else None
            if macro:
                items.append(Item(label, value=lambda m=macro: m,
                                  action=lambda m=macro: self._macro(m)))
        items += [
            Item("Length", value=lambda: f"{self.length:g} mm",
                 edit=NumberEdit(lambda: self.length, self._set_length, 1, 200, step=1)),
            Item("Speed", value=lambda: f"{self.speed:g} mm/s",
                 edit=NumberEdit(lambda: self.speed, self._set_speed, 1, 30, step=1)),
            Item("Extrude", action=lambda: self._move(self.length)),
            Item("Retract", action=lambda: self._move(-self.length)),
        ]
        self.set_items(items)

    def _heat(self) -> None:
        self.app.push(HeatScreen(self.app))

    def _macro(self, name: str) -> None:
        self.api.gcode_fire(name)
        self.app.toast(f"Running {name}")

    def _set_length(self, v: float) -> None:
        self.length = v
        self.app.settings.set(self.api, "extrude_length", v)

    def _set_speed(self, v: float) -> None:
        self.speed = v
        self.app.settings.set(self.api, "extrude_speed", v)

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


class HeatScreen(ListScreen):
    """Pick a preset's hotend temperature (the bed is left alone)."""
    title = "Heat hotend"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.set_items([Item(p.name, value=lambda p=p: f"{p.extruder:.0f}°",
                             action=lambda p=p: self._pick(p)) for p in self.cfg.presets])

    def _pick(self, preset) -> None:
        set_target(self, "extruder", preset.extruder)
        self.app.toast(f"Heating to {preset.extruder:.0f}° ({preset.name})")
        self.app.pop()
