"""Fans and lights, discovered from Klipper's object list.

Manual fans (the part fan, fan_generic) and lights get editors; fans that
Klipper runs itself (heater, controller and temperature fans) are shown as
"auto" with their current speed.
"""

from __future__ import annotations

import asyncio
import logging

from ...printer import FAN_TYPES, LED_TYPES, LIGHT_TYPES, MANUAL_FANS, sensor_label
from ..screen import Item, ListScreen, NumberEdit

log = logging.getLogger(__name__)


def short(name: str) -> str:
    """'fan_generic exhaust' -> 'exhaust' (what gcode commands expect)."""
    return name.split(None, 1)[-1]


class FansScreen(ListScreen):
    title = "Fans"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.empty_text = "No fans"
        # Fans you can set first (the part fan leads), then the automatic ones.
        fans = sorted(self.printer.devices(FAN_TYPES), key=lambda n: n.split()[0] not in MANUAL_FANS)
        self.set_items([self._item(n) for n in fans])

    def _speed(self, name: str) -> float:
        return float(self.printer.get(name, "speed", 0.0)) * 100

    def _item(self, name: str) -> Item:
        kind = name.split()[0]
        if kind not in MANUAL_FANS:
            return Item(sensor_label(name), value=lambda: f"auto {self._speed(name):3.0f}%")
        if kind == "fan":
            commit = lambda v: self.api.gcode_fire(f"M106 S{round(v * 2.55)}")  # noqa: E731
        else:
            commit = lambda v: self.api.gcode_fire(  # noqa: E731
                f"SET_FAN_SPEED FAN={short(name)} SPEED={v / 100:.2f}")
        return Item(sensor_label(name), value=lambda: f"{self._speed(name):.0f}%",
                    edit=NumberEdit(lambda: self._speed(name), commit, 0, 100, step=5, fmt="{:.0f}%"))


class LightsScreen(ListScreen):
    title = "Lights"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.empty_text = "Loading..."

    def on_enter(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        # Whether an output_pin can dim (pwm) and its scale only show in the config.
        try:
            res = await self.api.call("printer.objects.query", objects={"configfile": ["settings"]})
            settings = res.get("status", {}).get("configfile", {}).get("settings", {})
        except Exception as e:  # noqa: BLE001 - assume on/off pins
            log.info("no config settings: %s", e)
            settings = {}
        items = []
        for name in self.printer.devices(LIGHT_TYPES):
            if name.split()[0] in LED_TYPES:
                items.append(self._led(name))
            else:
                section = settings.get(name.lower(), {})
                items.append(self._pin(name, bool(section.get("pwm")), float(section.get("scale", 1.0))))
        self.set_items(items)
        self.empty_text = "No lights"
        self.app.invalidate()

    def _pin(self, name: str, pwm: bool, scale: float) -> Item:
        def value() -> float:
            return float(self.printer.get(name, "value", 0.0))

        def set_pin(fraction: float) -> None:
            self.api.gcode_fire(f"SET_PIN PIN={short(name)} VALUE={fraction * scale:.2f}")

        if pwm:
            return Item(sensor_label(name), value=lambda: f"{value() * 100:.0f}%",
                        edit=NumberEdit(lambda: value() * 100, lambda v: set_pin(v / 100), 0, 100,
                                        step=5, fmt="{:.0f}%"))
        return Item(sensor_label(name), value=lambda: "on" if value() else "off",
                    action=lambda: set_pin(0.0 if value() else 1.0))

    def _led(self, name: str) -> Item:
        def brightness() -> float:
            data = self.printer.get(name, "color_data", [[0, 0, 0, 0]])
            return max(data[0][:3]) * 100 if data else 0.0

        def commit(v: float) -> None:
            x = v / 100
            self.api.gcode_fire(f"SET_LED LED={short(name)} RED={x:.2f} GREEN={x:.2f} BLUE={x:.2f}")

        return Item(sensor_label(name), value=lambda: f"{brightness():.0f}%",
                    edit=NumberEdit(brightness, commit, 0, 100, step=5, fmt="{:.0f}%"))
