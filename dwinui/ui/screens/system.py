"""Restarts, emergency stop, host power and backlight."""

from __future__ import annotations

import asyncio

from ... import __version__
from ..screen import Confirm, Item, ListScreen, NumberEdit


class SystemScreen(ListScreen):
    title = "System"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.set_items([
            Item("Emergency stop", action=self._estop),
            Item("Firmware restart", action=lambda: self._confirm(
                "Firmware restart?", "Restarts Klipper and all MCUs.", "printer.firmware_restart")),
            Item("Restart Klipper", action=lambda: self._confirm(
                "Restart Klipper?", "Reloads printer.cfg.", "printer.restart")),
            Item("Reboot host", action=lambda: self._confirm(
                "Reboot host?", "The Raspberry Pi will reboot.", "machine.reboot")),
            Item("Shutdown host", action=lambda: self._confirm(
                "Shutdown host?", "The Raspberry Pi will power off.", "machine.shutdown")),
            *([Item("Power", value=self._power_summary, action=self._power)] if self.printer.power else []),
            Item("Brightness", value=lambda: f"{self.app.brightness}",
                 edit=NumberEdit(lambda: self.app.brightness,
                                 lambda v: asyncio.ensure_future(self.app.set_brightness(int(v))),
                                 8, 255, step=8)),
            Item("Reset display settings", action=self._reset_settings,
                 enabled=lambda: bool(self.app.settings.values)),
            Item("Version", value=lambda: __version__),
        ])

    def _reset_settings(self) -> None:
        def reset() -> None:
            async def run() -> None:
                await self.app.settings.reset(self.api)
                self.app.toast("Display settings reset")
            asyncio.ensure_future(run())
        self.app.push(Confirm(self.app, "Reset settings?",
                              "Brightness, jog step and extrude values go back to dwinui.conf.",
                              reset, ok="Reset"))

    def _power_summary(self) -> str:
        n = len(self.printer.power)
        return f"{n} device" + ("" if n == 1 else "s")

    def _power(self) -> None:
        from .power import PowerScreen
        self.app.push(PowerScreen(self.app))

    def _estop(self) -> None:
        if self.cfg.confirm_estop:
            self._confirm("EMERGENCY STOP?", "Halts the printer immediately.",
                          "printer.emergency_stop", default_ok=True)
        else:
            self.api.fire("printer.emergency_stop")

    def _confirm(self, title: str, message: str, method: str, default_ok: bool = False) -> None:
        self.app.push(Confirm(self.app, title, message, lambda: self.api.fire(method),
                              ok="Yes", danger=True, default_ok=default_ok))
