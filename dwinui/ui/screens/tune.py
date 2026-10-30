"""Live print tuning: speed, flow, fan, babystepping, temperatures."""

from __future__ import annotations

import asyncio

from ..screen import Adjust, Item, ListScreen, NumberEdit
from .common import heater_item


class TuneScreen(ListScreen):
    title = "Tune"

    def __init__(self, app) -> None:
        super().__init__(app)
        self._z_pending = 0.0
        self._z_worker: asyncio.Task | None = None
        pr = self.printer
        speed = lambda: pr.get("gcode_move", "speed_factor", 1.0) * 100  # noqa: E731
        flow = lambda: pr.get("gcode_move", "extrude_factor", 1.0) * 100  # noqa: E731
        fan = lambda: pr.get("fan", "speed", 0.0) * 100  # noqa: E731
        self.set_items([
            Item("Speed", value=lambda: f"{speed():.0f}%",
                 edit=NumberEdit(speed, lambda v: self.api.gcode_fire(f"M220 S{v:.0f}"),
                                 10, 300, step=5, fmt="{:.0f}%")),
            Item("Flow", value=lambda: f"{flow():.0f}%",
                 edit=NumberEdit(flow, lambda v: self.api.gcode_fire(f"M221 S{v:.0f}"),
                                 50, 150, step=1, fmt="{:.0f}%")),
            Item("Fan", value=lambda: f"{fan():.0f}%",
                 edit=NumberEdit(fan, lambda v: self.api.gcode_fire(f"M106 S{round(v * 2.55)}"),
                                 0, 100, step=5, fmt="{:.0f}%")),
            Item("Z offset", value=lambda: f"{pr.z_offset:+.3f}",
                 edit=Adjust(self._babystep, lambda: f"{pr.z_offset + self._z_pending:+.3f}")),
            heater_item(self, "extruder"),
            heater_item(self, "heater_bed"),
        ])

    def _babystep(self, delta: int) -> None:
        self._z_pending += delta * self.cfg.babystep
        if self._z_worker is None or self._z_worker.done():
            self._z_worker = asyncio.ensure_future(self._drain())

    async def _drain(self) -> None:
        while abs(self._z_pending) > 1e-9:
            adjust, self._z_pending = self._z_pending, 0.0
            try:
                await self.api.gcode(f"SET_GCODE_OFFSET Z_ADJUST={adjust:.3f} MOVE=1")
            except Exception as e:  # noqa: BLE001
                self._z_pending = 0.0
                self.app.toast(str(e), "error")
                return
