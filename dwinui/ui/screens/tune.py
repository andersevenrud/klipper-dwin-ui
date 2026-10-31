"""Live print tuning: speed, flow and babystepping. Temperatures and fans
have their own screens."""

from __future__ import annotations

import asyncio

from ..screen import Adjust, Confirm, Item, ListScreen, NumberEdit


class TuneScreen(ListScreen):
    title = "Tune"

    def __init__(self, app) -> None:
        super().__init__(app)
        self._z_pending = 0.0
        self._z_worker: asyncio.Task | None = None
        pr = self.printer
        speed = lambda: pr.get("gcode_move", "speed_factor", 1.0) * 100  # noqa: E731
        flow = lambda: pr.get("gcode_move", "extrude_factor", 1.0) * 100  # noqa: E731
        self.set_items([
            Item("Speed", value=lambda: f"{speed():.0f}%",
                 edit=NumberEdit(speed, lambda v: self.api.gcode_fire(f"M220 S{v:.0f}"),
                                 10, 300, step=5, fmt="{:.0f}%")),
            Item("Flow", value=lambda: f"{flow():.0f}%",
                 edit=NumberEdit(flow, lambda v: self.api.gcode_fire(f"M221 S{v:.0f}"),
                                 50, 150, step=1, fmt="{:.0f}%")),
            Item("Z offset", value=lambda: f"{pr.z_offset:+.3f}",
                 edit=Adjust(self._babystep, lambda: f"{pr.z_offset + self._z_pending:+.3f}")),
            Item("Save Z offset", action=self._save_offset,
                 enabled=lambda: abs(pr.z_offset) >= 0.0005),
        ])

    def _save_offset(self) -> None:
        """Fold the babystep offset into the probe's (or Z endstop's) config.
        SAVE_CONFIG restarts Klipper, so during a print it waits for later."""
        command = "Z_OFFSET_APPLY_PROBE" if self.printer.has_probe else "Z_OFFSET_APPLY_ENDSTOP"

        async def run() -> None:
            try:
                await self.api.gcode(command)
            except Exception as e:  # noqa: BLE001
                self.app.toast(str(e), "error")
                return
            if self.printer.job_active:
                self.app.toast("Z offset applied; save it after the print (System > Save config)", seconds=5)
                return
            self.app.push(Confirm(self.app, "Save config?",
                                  "Writes the new Z offset to printer.cfg and restarts Klipper.",
                                  lambda: self.api.gcode_fire("SAVE_CONFIG"), ok="Save"))
        asyncio.ensure_future(run())

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
