"""Printer state as seen through Moonraker subscriptions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

SUBSCRIBE_OBJECTS = (
    "print_stats", "display_status", "extruder", "heater_bed", "toolhead",
    "gcode_move", "fan", "virtual_sdcard", "idle_timeout", "webhooks",
)


class Printer:
    def __init__(self) -> None:
        self.connected = False          # websocket to Moonraker
        self.klippy_state = "disconnected"
        self.state_message = ""
        self.status: dict[str, dict[str, Any]] = {}
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
    def homed_axes(self) -> str:
        return self.get("toolhead", "homed_axes", "")
