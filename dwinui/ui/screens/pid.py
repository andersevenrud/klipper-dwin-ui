"""PID tuning: pick a temperature (a preset's, or any), confirm, and let
Klipper's PID_CALIBRATE run. It can take minutes and can only be stopped
with an emergency stop; the result dialog comes from the app, which also
catches tunings started elsewhere."""

from __future__ import annotations

import re

from ..screen import Confirm, Item, ListScreen, NumberEdit
from .common import HEATERS

RESULT = re.compile(r"pid_Kp=([\d.]+) pid_Ki=([\d.]+) pid_Kd=([\d.]+)")


def parse_result(line: str) -> tuple[str, str, str] | None:
    m = RESULT.search(line)
    return (m.group(1), m.group(2), m.group(3)) if m else None


def tuned_heater(pending: dict, kp: str) -> str | None:
    """The heater SAVE_CONFIG would write these values to."""
    for section, options in (pending or {}).items():
        if str((options or {}).get("pid_kp", "")) == kp:
            return section
    return None


def result_dialog(app, values: tuple[str, str, str], heater: str | None) -> Confirm:
    kp, ki, kd = values
    name = HEATERS.get(heater or "", heater or "")
    return Confirm(app, "PID tuned", f"{name + ': ' if name else ''}Kp {kp}, Ki {ki}, Kd {kd}. "
                   "Save writes them to printer.cfg and restarts Klipper.",
                   lambda: app.api.gcode_fire("SAVE_CONFIG"), ok="Save", cancel="Later")


class PidScreen(ListScreen):
    def __init__(self, app, heater: str) -> None:
        self.heater = heater
        self.title = f"PID {HEATERS[heater].lower()}"
        super().__init__(app)
        hotend = heater == "extruder"
        temps = sorted({p.extruder if hotend else p.heater_bed for p in self.cfg.presets} - {0})
        names = {}
        for p in self.cfg.presets:
            names.setdefault(p.extruder if hotend else p.heater_bed, p.name)
        lo, hi = (150, self.cfg.max_extruder_temp) if hotend else (40, self.cfg.max_bed_temp)
        self.custom = temps[0] if temps else (200.0 if hotend else 60.0)
        items = [Item(names[temp], value=lambda t=temp: f"{t:.0f}°", action=lambda t=temp: self._ask(t))
                 for temp in temps]
        items.append(Item("Custom", value=lambda: f"{self.custom:.0f}°",
                          edit=NumberEdit(lambda: self.custom, self._custom, lo, hi, step=5)))
        self.set_items(items)

    def _custom(self, value: float) -> None:
        self.custom = value
        self._ask(value)

    def _ask(self, target: float) -> None:
        label = HEATERS[self.heater].lower()

        def start() -> None:
            self.api.gcode_fire(f"PID_CALIBRATE HEATER={self.heater} TARGET={target:.0f}")
            self.app.toast(f"PID tuning the {label} at {target:.0f}°", seconds=5)
            self.app.pop_to_root()

        self.app.push(Confirm(self.app, f"Tune at {target:.0f}°?",
                              f"Heats the {label} and cycles it for several minutes. "
                              "Only an emergency stop can stop it.", start, ok="Start", danger=True))
