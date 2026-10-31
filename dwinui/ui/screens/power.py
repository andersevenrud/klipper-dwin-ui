"""Moonraker [power] devices (printer supply, relays): on/off from System."""

from __future__ import annotations

from ...printer import device_label
from ..screen import Confirm, Item, ListScreen


class PowerScreen(ListScreen):
    title = "Power"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.empty_text = "No power devices"
        self.set_items([self._item(name) for name in sorted(self.printer.power)])

    def _status(self, name: str) -> str:
        return str(self.printer.power.get(name, {}).get("status", "?"))

    def _item(self, name: str) -> Item:
        return Item(device_label(name), value=lambda: self._status(name),
                    action=lambda: self._toggle(name),
                    enabled=lambda: not self.printer.power_locked(name))

    def _toggle(self, name: str) -> None:
        def post(action: str) -> None:
            self.api.fire("machine.device_power.post_device", device=name, action=action)

        if self._status(name) == "on":
            self.app.push(Confirm(self.app, f"Turn off {device_label(name)}?",
                                  "Whatever it powers switches off immediately.",
                                  lambda: post("off"), ok="Turn off", danger=True))
        else:
            post("on")
