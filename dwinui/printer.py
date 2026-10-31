"""Printer state as seen through Moonraker subscriptions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

SUBSCRIBE_OBJECTS = (
    "print_stats", "display_status", "extruder", "heater_bed", "toolhead",
    "gcode_move", "fan", "virtual_sdcard", "idle_timeout", "webhooks", "heaters",
)
SENSOR_LABELS = {"extruder": "Hotend", "heater_bed": "Bed", "fan": "Part fan"}

# Fans the user sets; the rest are run by Klipper (shown, not editable).
MANUAL_FANS = ("fan", "fan_generic")
FAN_TYPES = (*MANUAL_FANS, "heater_fan", "controller_fan", "temperature_fan")
LED_TYPES = ("led", "neopixel", "dotstar", "pca9533", "pca9632")
LIGHT_TYPES = ("output_pin", *LED_TYPES)
RUNOUT_TYPES = ("filament_switch_sensor", "filament_motion_sensor")
DEVICE_TYPES = (*FAN_TYPES, *LIGHT_TYPES, *RUNOUT_TYPES)


def sensor_label(name: str) -> str:
    """'temperature_sensor cartographer_coil' -> 'Cartographer Coil' (each word
    gets a capital first letter; the rest is kept, so 'MCU' stays 'MCU')."""
    if name in SENSOR_LABELS:
        return SENSOR_LABELS[name]
    words = name.split(None, 1)[-1].replace("_", " ").split()
    return " ".join(w[:1].upper() + w[1:] for w in words)


def device_label(name: str) -> str:
    """Moonraker power device name -> label: 'printer_psu' -> 'Printer Psu'."""
    return " ".join(w[:1].upper() + w[1:] for w in name.replace("_", " ").split())


def _heater_order(name: str) -> tuple[int, str]:
    return (0 if name.startswith("extruder") else 1 if name == "heater_bed" else 2, name)


class Printer:
    def __init__(self) -> None:
        self.connected = False          # websocket to Moonraker
        self.klippy_state = "disconnected"
        self.state_message = ""
        self.status: dict[str, dict[str, Any]] = {}
        self.objects: list[str] = []     # every object Klipper has (printer.objects.list)
        # Moonraker [power] devices: name -> {"status": "on"|"off"|..., "locked_while_printing": bool}
        self.power: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[], None]] = []

    def listen(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def changed(self) -> None:
        for fn in self._listeners:
            fn()

    def replace(self, status: dict[str, dict[str, Any]]) -> None:
        self.status = {k: dict(v) for k, v in status.items()}
        self._sync_webhooks()
        self.changed()

    def update(self, status: dict[str, dict[str, Any]]) -> None:
        for obj, fields in status.items():
            self.status.setdefault(obj, {}).update(fields)
        self._sync_webhooks()
        self.changed()

    def set_state(self, state: str, message: str = "") -> None:
        if (state, message) != (self.klippy_state, self.state_message):
            self.klippy_state = state
            self.state_message = message
            self.changed()

    def _sync_webhooks(self) -> None:
        wh = self.status.get("webhooks")
        if wh and "state" in wh:
            self.klippy_state = wh["state"]
            self.state_message = wh.get("state_message", "")

    def get(self, obj: str, field: str, default: Any = None) -> Any:
        value = self.status.get(obj, {}).get(field)
        return default if value is None else value

    @property
    def ready(self) -> bool:
        return self.connected and self.klippy_state == "ready"

    @property
    def job_state(self) -> str:
        return self.get("print_stats", "state", "standby")

    @property
    def job_active(self) -> bool:
        return self.job_state in ("printing", "paused")

    @property
    def progress(self) -> float:
        return float(self.get("display_status", "progress",
                              self.get("virtual_sdcard", "progress", 0.0)))

    def temp(self, heater: str) -> tuple[float, float]:
        return float(self.get(heater, "temperature", 0.0)), float(self.get(heater, "target", 0.0))

    def position(self, axis: int) -> float:
        pos = self.get("gcode_move", "gcode_position", [0.0, 0.0, 0.0, 0.0])
        return float(pos[axis]) if len(pos) > axis else 0.0

    @property
    def z_offset(self) -> float:
        origin = self.get("gcode_move", "homing_origin", [0.0, 0.0, 0.0, 0.0])
        return float(origin[2]) if len(origin) > 2 else 0.0

    @property
    def has_probe(self) -> bool:
        return "probe" in self.objects

    @property
    def save_config_pending(self) -> bool:
        return bool(self.get("configfile", "save_config_pending", False))

    @property
    def homed_axes(self) -> str:
        return self.get("toolhead", "homed_axes", "")

    def macro(self, name: str) -> str | None:
        """The macro's name as Klipper knows it, if one by that name exists
        (G-code commands are case-insensitive)."""
        for obj in self.objects:
            kind, _, macro = obj.partition(" ")
            if kind == "gcode_macro" and macro.upper() == name.upper():
                return macro
        return None

    def runout(self) -> str | None:
        """Label of an enabled filament sensor that sees no filament."""
        for obj in self.objects:
            if obj.split()[0] in RUNOUT_TYPES:
                st = self.status.get(obj, {})
                if st.get("enabled", True) and st.get("filament_detected") is False:
                    return sensor_label(obj)
        return None

    def devices(self, types: tuple[str, ...]) -> list[str]:
        """Objects of the given types, without "_hidden" ones; the part fan
        first, then sorted by label."""
        found = [o for o in self.objects
                 if o.split()[0] in types and not o.split()[-1].startswith("_")]
        return sorted(found, key=lambda o: (o != "fan", sensor_label(o).lower()))

    def printer_supplies(self) -> list[str]:
        """Power devices that feed the printer: the ones locked while printing,
        which is how a printer's supply is normally set up. If none is marked
        that way, all devices (we can't tell them apart)."""
        locked = [n for n, d in self.power.items() if d.get("locked_while_printing")]
        return sorted(locked or self.power)

    def power_locked(self, name: str) -> bool:
        return bool(self.power.get(name, {}).get("locked_while_printing")) and self.job_active

    @property
    def heater_names(self) -> list[str]:
        """Controllable heaters, hotend(s) first, then bed, then the rest."""
        return sorted(self.get("heaters", "available_heaters", []), key=_heater_order)

    @property
    def sensor_names(self) -> list[str]:
        """Hotend(s) and bed first, then everything else sorted by label."""
        names = dict.fromkeys([*self.heater_names, *self.get("heaters", "available_sensors", [])])
        first = [n for n in names if n.startswith("extruder") or n == "heater_bed"]
        rest = sorted((n for n in names if n not in first), key=lambda n: sensor_label(n).lower())
        return sorted(first, key=_heater_order) + rest
