"""Print job screen. It replaces the dashboard while a job runs, and stays
after the job ends (showing Complete/Cancelled) until dismissed with a click."""

from __future__ import annotations

import asyncio
import time

from ... import protocol as p
from .. import theme as t
from ..screen import HeaderScreen
from ..widgets import Bar, Icon, Text
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


class PrintingScreen(HeaderScreen):
    title = "Printing"

    def __init__(self, app) -> None:
        super().__init__(app)
        self._metadata: dict = {}
        self._metadata_for: str | None = None

        labels = []

        def label(key, x, y, s):
            ref = app.assets.icon("small", key)
            if ref:
                labels.append(Icon(f"st.{key}.i", x, y - 2, ref))
                x += ref.w + 4
            labels.append(Text(f"st.{key}.l", x, y, len(s), font=1, fg=t.DIM, value=s))

        T = t.HEADER_H   # layout below the title bar
        self.filename = Text("st.file", 8, T + 10, 32, font=1)
        self.message = Text("st.msg", 8, T + 30, 32, font=1, fg=t.DIM)
        self.hotend = Text("st.hotend", 8, T + 74, 9, font=4, fg=t.HOT)
        self.bed = Text("st.bed", COL2, T + 74, 9, font=4, fg=t.HOT)
        self.speed = Text("st.speed", 8, T + 138, 6, font=3)
        self.flow = Text("st.flow", 96, T + 138, 6, font=3)
        self.fan = Text("st.fan", COL3, T + 138, 6, font=3)
        self.z = Text("st.z", 8, T + 200, 7, font=3)
        self.offset = Text("st.offset", 96, T + 200, 7, font=3)
        self.filament = Text("st.filament", COL3, T + 200, 7, font=3)
        self.bar = Bar("st.bar", (8, T + 246, p.WIDTH - 9, T + 265))
        self.percent = Text("st.pct", 8, T + 272, 7, font=3)
        self.elapsed = Text("st.elapsed", 8, T + 324, 10, font=3)
        self.remaining = Text("st.remaining", COL2, T + 324, 10, font=3)
        self.finish = Text("st.finish", 8, T + 376, 22, font=2, fg=t.DIM)
        self.hint = Text("st.hint", 0, 452, 34, font=1, fg=t.DIM, align="center")
        label("hotend", 8, T + 56, "Hotend")
        label("bed", COL2, T + 56, "Bed")
        label("speed", 8, T + 120, "Speed")
        label("flow", 96, T + 120, "Flow")
        label("fan", COL3, T + 120, "Fan")
        label("z", 8, T + 182, "Z")
        label("offset", 96, T + 182, "Offset")
        label("filament", COL3, T + 182, "Filament")
        label("elapsed", 8, T + 306, "Elapsed")
        label("remaining", COL2, T + 306, "Remaining")
        self.widgets += labels + [
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

        # Only once the job has ended: a press then opens Home / Reprint.
        self.hint.value = "" if pr.job_active else "Press to continue"
        filename = pr.get("print_stats", "filename", "")
        self.filename.value = filename or "No file loaded"
        self.message.value = pr.get("display_status", "message", "") or pr.get("print_stats", "message", "")
        if filename and filename != self._metadata_for:
            self._metadata_for = filename
            self._metadata = {}
            asyncio.ensure_future(self._load_metadata(filename))

        for widget, heater, color in ((self.hotend, "extruder", t.HOT), (self.bed, "heater_bed", t.BED)):
            cur, target = pr.temp(heater)
            widget.value = f"{cur:.0f}/{target:.0f}°"
            widget.fg = color if target > 0 else t.FG
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
        if not self.printer.job_active:
            self._finished_dialog()
            return
        from .menu import MenuScreen
        self.app.push(MenuScreen(self.app))

    def _finished_dialog(self) -> None:
        """After a job: back to the home screen, or the same file again."""
        from ..screen import Confirm

        state = self.printer.job_state
        title = {"complete": "Print complete", "cancelled": "Print cancelled"}.get(state, "Print failed")
        filename = self.printer.get("print_stats", "filename", "")

        def again() -> None:
            self.api.fire("printer.print.start", filename=filename)

        self.app.push(Confirm(self.app, title, filename, again, ok="Reprint", cancel="Home",
                              on_cancel=self.app.dismiss_job))

    def on_back(self) -> None:
        pass
