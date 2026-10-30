"""Root screen: print status at a glance. Click opens the menu."""

from __future__ import annotations

import asyncio
import time

from ... import protocol as p
from .. import theme as t
from ..screen import HeaderScreen
from ..widgets import Bar, Text
from .common import fmt_duration

STATE_LABELS = {
    "standby": ("Ready", t.FG),
    "printing": ("Printing", t.OK),
    "paused": ("Paused", t.WARN),
    "complete": ("Complete", t.OK),
    "cancelled": ("Cancelled", t.WARN),
    "error": ("Error", t.ERROR),
}

COL2 = 140
COL3 = 184


class StatusScreen(HeaderScreen):
    title = "Ready"

    def __init__(self, app) -> None:
        super().__init__(app)
        self._metadata: dict = {}
        self._metadata_for: str | None = None

        def label(key, x, y, s):
            return Text(f"st.{key}.l", x, y, 12, font=1, fg=t.DIM, value=s)

        self.filename = Text("st.file", 8, 46, 32, font=1)
        self.message = Text("st.msg", 8, 66, 32, font=1, fg=t.DIM)
        self.hotend = Text("st.hotend", 8, 110, 9, font=4, fg=t.HOT)
        self.bed = Text("st.bed", COL2, 110, 9, font=4, fg=t.HOT)
        self.speed = Text("st.speed", 8, 174, 6, font=3)
        self.flow = Text("st.flow", 96, 174, 6, font=3)
        self.fan = Text("st.fan", COL3, 174, 6, font=3)
        self.z = Text("st.z", 8, 236, 7, font=3)
        self.offset = Text("st.offset", 96, 236, 7, font=3)
        self.filament = Text("st.filament", COL3, 236, 7, font=3)
        self.bar = Bar("st.bar", (8, 282, p.WIDTH - 9, 301))
        self.percent = Text("st.pct", 8, 308, 7, font=3)
        self.elapsed = Text("st.elapsed", 8, 360, 10, font=3)
        self.remaining = Text("st.remaining", COL2, 360, 10, font=3)
        self.finish = Text("st.finish", 8, 412, 22, font=2, fg=t.DIM)
        self.hint = Text("st.hint", 0, 452, 34, font=1, fg=t.DIM, align="center",
                         value="press: menu")
        self.widgets += [
            label("he", 8, 92, "Hotend"), label("bed", COL2, 92, "Bed"),
            label("sp", 8, 156, "Speed"), label("fl", 96, 156, "Flow"), label("fan", COL3, 156, "Fan"),
            label("z", 8, 218, "Z"), label("off", 96, 218, "Z offset"),
            label("fil", COL3, 218, "Filament"),
            label("el", 8, 342, "Elapsed"), label("rem", COL2, 342, "Remaining"),
            self.filename, self.message, self.hotend, self.bed, self.speed, self.flow, self.fan,
            self.z, self.offset, self.filament, self.bar, self.percent, self.elapsed,
            self.remaining, self.finish, self.hint,
        ]

    def refresh(self) -> None:
        super().refresh()
        pr = self.printer
        state = pr.job_state
        label, color = STATE_LABELS.get(state, (state.title(), t.FG))
        self.header_title.value = label
        self.header_title.fg = color

        filename = pr.get("print_stats", "filename", "")
        self.filename.value = filename or "No file loaded"
        self.message.value = pr.get("display_status", "message", "") or pr.get("print_stats", "message", "")
        if filename and filename != self._metadata_for:
            self._metadata_for = filename
            self._metadata = {}
            asyncio.ensure_future(self._load_metadata(filename))

        for widget, heater in ((self.hotend, "extruder"), (self.bed, "heater_bed")):
            cur, target = pr.temp(heater)
            widget.value = f"{cur:.0f}/{target:.0f}"
            widget.fg = t.HOT if target > 0 else t.FG
        self.speed.value = f"{pr.get('gcode_move', 'speed_factor', 1.0) * 100:.0f}%"
        self.flow.value = f"{pr.get('gcode_move', 'extrude_factor', 1.0) * 100:.0f}%"
        self.fan.value = f"{pr.get('fan', 'speed', 0.0) * 100:.0f}%"
        self.z.value = f"{pr.position(2):.2f}"
        self.offset.value = f"{pr.z_offset:+.3f}"
        self.filament.value = f"{pr.get('print_stats', 'filament_used', 0.0) / 1000:.2f}m"

        progress = pr.progress
        self.bar.value = progress
        self.bar.fg = t.WARN if state == "paused" else t.ACCENT
        self.percent.value = f"{progress * 100:.1f}%"

        elapsed = pr.get("print_stats", "print_duration", 0.0)
        remaining = self._remaining(progress, elapsed) if pr.job_active else None
        self.elapsed.value = fmt_duration(elapsed) if filename else "--"
        self.remaining.value = fmt_duration(remaining)
        if remaining is not None:
            self.finish.value = "Done at " + time.strftime("%H:%M", time.localtime(time.time() + remaining))
        else:
            self.finish.value = ""

    def _remaining(self, progress: float, elapsed: float) -> float | None:
        estimate = self._metadata.get("estimated_time")
        if progress >= 0.05 and elapsed > 0:
            return max(0.0, elapsed / progress - elapsed)
        if estimate:
            return max(0.0, estimate - elapsed)
        return None

    async def _load_metadata(self, filename: str) -> None:
        try:
            meta = await self.api.call("server.files.metadata", filename=filename)
        except Exception:  # noqa: BLE001 - metadata is optional
            return
        if filename == self._metadata_for:
            self._metadata = meta or {}
            self.app.invalidate()

    def on_click(self) -> None:
        from .menu import MenuScreen
        self.app.push(MenuScreen(self.app))

    def on_back(self) -> None:
        pass
