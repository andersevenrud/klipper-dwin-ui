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
            Item("Brightness", value=lambda: f"{self.app.brightness}",
                 edit=NumberEdit(lambda: self.app.brightness,
                                 lambda v: asyncio.ensure_future(self.app.set_brightness(int(v))),
                                 8, 255, step=8)),
            Item("Version", value=lambda: __version__),
        ])

    def _estop(self) -> None:
        if self.cfg.confirm_estop:
            self._confirm("EMERGENCY STOP?", "Halts the printer immediately.",
                          "printer.emergency_stop", default_ok=True)
        else:
            self.api.fire("printer.emergency_stop")

    def _confirm(self, title: str, message: str, method: str, default_ok: bool = False) -> None:
        self.app.push(Confirm(self.app, title, message, lambda: self.api.fire(method),
                              ok="Yes", danger=True, default_ok=default_ok))
