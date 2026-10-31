"""Homing and encoder jogging.

Click an axis row to enter jog mode; each detent moves one step. Detents
that arrive while a move is in flight are summed into the next move, so a
fast spin never queues dozens of tiny moves.
"""

from __future__ import annotations

import asyncio

from ..screen import Adjust, Item, ListScreen
from .common import jog_script

STEPS = (0.1, 1.0, 10.0, 50.0)
AXES = "XYZ"


class MoveScreen(ListScreen):
    title = "Move"

    def __init__(self, app) -> None:
        super().__init__(app)
        saved = float(self.app.settings.get("jog_step", 1.0))
        self.step = saved if saved in STEPS else 1.0
        self._pending = {a: 0.0 for a in AXES}
        self._worker: asyncio.Task | None = None
        items = [
            Item("Home all", action=lambda: self._home("G28")),
            Item("Home XY", action=lambda: self._home("G28 X Y")),
            Item("Home Z", action=lambda: self._home("G28 Z")),
            Item("Step", value=lambda: f"{self.step:g} mm", action=self._next_step),
        ]
        for i, axis in enumerate(AXES):
            items.append(Item(f"Jog {axis}", edit=Adjust(
                lambda d, a=axis: self._jog(a, d),
                lambda a=axis, i=i: self._pos(a, i)),
                value=lambda a=axis, i=i: self._pos(a, i)))
        items.append(Item("Disable motors", action=self._motors_off,
                          enabled=lambda: not self.printer.job_active))
        self.set_items(items)

    def _pos(self, axis: str, index: int) -> str:
        homed = axis.lower() in self.printer.homed_axes
        return f"{self.printer.position(index):.1f}" if homed else "unhomed"

    def _home(self, script: str) -> None:
        self.app.toast("Homing...")
        self.api.gcode_fire(script)

    def _next_step(self) -> None:
        self.step = STEPS[(STEPS.index(self.step) + 1) % len(STEPS)]
        self.app.settings.set(self.api, "jog_step", self.step)

    def _motors_off(self) -> None:
        self.api.gcode_fire("M84")
        self.app.toast("Motors off")

    def _jog(self, axis: str, delta: int) -> None:
        self._pending[axis] += delta * self.step
        if self._worker is None or self._worker.done():
            self._worker = asyncio.ensure_future(self._drain())

    async def _drain(self) -> None:
        while any(self._pending.values()):
            for axis in AXES:
                dist = self._pending[axis]
                if not dist:
                    continue
                self._pending[axis] = 0.0
                feed = self.cfg.z_feedrate if axis == "Z" else self.cfg.xy_feedrate
                try:
                    await self.api.gcode(jog_script(axis, dist, feed))
                except Exception as e:  # noqa: BLE001
                    self._pending = {a: 0.0 for a in AXES}
                    self.app.toast(str(e), "error")
                    return
