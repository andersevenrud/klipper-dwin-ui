"""Shown instead of the normal UI while Moonraker or Klippy isn't ready.

Offers what can actually help: the printer's power supply (if Moonraker
controls it) - switch it on when it is off, the classic reason the MCU is
unreachable, or power-cycle it when it is on, which revives a hung board -
and restarting Klipper once it is in a shutdown or error state.
"""

from __future__ import annotations

import asyncio
import time

from ... import protocol as p
from ...printer import device_label
from .. import theme as t
from ..screen import Item, ListScreen
from ..widgets import Text, wrap

TITLES = {
    "disconnected": ("Disconnected", t.WARN),
    "startup": ("Starting...", t.FG),
    "shutdown": ("Shutdown", t.ERROR),
    "error": ("Klipper error", t.ERROR),
}
MSG_LINES = 8
CYCLE_OFF_SECONDS = 3.0      # how long a power cycle leaves the supply off
ARM_SECONDS = 4.0            # a "press again" confirmation expires after this


class BlockerScreen(ListScreen):
    title = "Klipper"
    back_row = False   # nothing to go back to until Klipper is ready

    def __init__(self, app) -> None:
        super().__init__(app)
        self.list.area = (0, 260, p.WIDTH - 1, p.HEIGHT - 50)
        self.lines = [Text(f"blk.l{i}", 8, t.HEADER_H + 14 + i * 24, 32, font=1, fg=t.DIM)
                      for i in range(MSG_LINES)]
        self.widgets += self.lines
        self._actions: tuple = ()
        self._armed: str | None = None       # device awaiting a second press to cycle
        self._armed_at = 0.0

    def _wanted_actions(self) -> tuple:
        pr = self.printer
        if not pr.connected:
            return ()
        power = tuple((d, pr.power[d].get("status") == "on") for d in pr.printer_supplies())
        restart = ("restart",) if pr.klippy_state in ("shutdown", "error") else ()
        # After an emergency stop (shutdown) a restart is the fix; an unreachable
        # MCU (error, startup) usually means its supply is off, so power comes first.
        return restart + power if pr.klippy_state == "shutdown" else power + restart

    def _rebuild(self, actions: tuple) -> None:
        items = []
        for action in actions:
            if action == "restart":
                items += [
                    Item("Firmware restart", action=lambda: self.api.fire("printer.firmware_restart")),
                    Item("Restart Klipper", action=lambda: self.api.fire("printer.restart")),
                ]
            else:
                device, on = action
                if on:
                    items.append(Item(lambda d=device: self._cycle_label(d),
                                      action=lambda d=device: self._cycle_pressed(d)))
                else:
                    items.append(Item(f"Power on: {device_label(device)}",
                                      action=lambda d=device: self._power_on(d)))
        self._actions = actions
        self.set_items(items)

    def _power_on(self, device: str) -> None:
        self.app.toast(f"Switching on {device_label(device)}")
        self.api.fire("machine.device_power.post_device", device=device, action="on")

    def _is_armed(self, device: str) -> bool:
        return self._armed == device and time.monotonic() - self._armed_at < ARM_SECONDS

    def _cycle_label(self, device: str) -> str:
        return "Press again to cycle" if self._is_armed(device) else f"Power cycle: {device_label(device)}"

    def _cycle_pressed(self, device: str) -> None:
        if not self._is_armed(device):
            self._armed, self._armed_at = device, time.monotonic()
            asyncio.get_running_loop().call_later(ARM_SECONDS, self.app.invalidate)
            return
        self._armed = None
        asyncio.ensure_future(self._power_cycle(device))

    async def _power_cycle(self, device: str) -> None:
        label = device_label(device)
        self.app.toast(f"Power cycling {label}", seconds=CYCLE_OFF_SECONDS + 1)
        try:
            await self.api.call("machine.device_power.post_device", device=device, action="off")
            await asyncio.sleep(CYCLE_OFF_SECONDS)
            await self.api.call("machine.device_power.post_device", device=device, action="on")
        except Exception as e:  # noqa: BLE001 - surface it; the rows show the real state
            self.app.toast(f"{label}: {e}", "error")

    def on_rotate(self, delta: int) -> None:
        self._armed = None
        super().on_rotate(delta)

    def refresh(self) -> None:
        actions = self._wanted_actions()
        if actions != self._actions:
            self._rebuild(actions)
        super().refresh()
        pr = self.printer
        state = pr.klippy_state if pr.connected else "disconnected"
        title, color = TITLES.get(state, (state.title(), t.FG))
        if not pr.connected:
            title = "No Moonraker"
        self.header_title.value = title
        self.header_title.fg = color
        message = pr.state_message or ("Waiting for Moonraker..." if not pr.connected else "")
        lines = wrap(message, 32, MSG_LINES)
        for i, widget in enumerate(self.lines):
            widget.value = lines[i] if i < len(lines) else ""

    def on_back(self) -> None:
        if self.editing:
            self.editing = None
