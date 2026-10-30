"""Heater targets and preheat presets."""

from __future__ import annotations

from ..screen import Item, ListScreen
from .common import heater_item, set_target


class TemperatureScreen(ListScreen):
    title = "Temperature"

    def __init__(self, app) -> None:
        super().__init__(app)
        items = [heater_item(self, "extruder"), heater_item(self, "heater_bed")]
        for preset in self.cfg.presets:
            items.append(Item(f"Preheat {preset.name}",
                              value=lambda p=preset: f"{p.extruder:.0f}/{p.heater_bed:.0f}",
                              action=lambda p=preset: self._preheat(p)))
        items.append(Item("Cooldown", action=self._cooldown))
        self.set_items(items)

    def _preheat(self, preset) -> None:
        set_target(self, "extruder", preset.extruder)
        set_target(self, "heater_bed", preset.heater_bed)
        self.app.toast(f"Preheating {preset.name}")

    def _cooldown(self) -> None:
        self.api.gcode_fire("TURN_OFF_HEATERS")
        self.app.toast("Heaters off")
