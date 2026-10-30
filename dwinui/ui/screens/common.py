"""Helpers shared between screens."""

from __future__ import annotations

from ..screen import Item, NumberEdit

HEATERS = {"extruder": "Hotend", "heater_bed": "Bed"}


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def heater_item(screen, heater: str) -> Item:
    """Row showing current/target with an inline target editor."""
    hi = screen.cfg.max_extruder_temp if heater == "extruder" else screen.cfg.max_bed_temp

    def value() -> str:
        cur, target = screen.printer.temp(heater)
        return f"{cur:.0f}/{target:.0f}°"

    def commit(v: float) -> None:
        set_target(screen, heater, v)

    return Item(HEATERS[heater], value=value,
                edit=NumberEdit(lambda: screen.printer.temp(heater)[1], commit, 0, hi, step=5))


def set_target(screen, heater: str, target: float) -> None:
    screen.api.gcode_fire(f"SET_HEATER_TEMPERATURE HEATER={heater} TARGET={target:.0f}")


def jog_script(axis: str, distance: float, feedrate: float) -> str:
    return ("SAVE_GCODE_STATE NAME=_dwinui_jog\nG91\n"
            f"G1 {axis}{distance:.3f} F{feedrate:.0f}\n"
            "RESTORE_GCODE_STATE NAME=_dwinui_jog")
