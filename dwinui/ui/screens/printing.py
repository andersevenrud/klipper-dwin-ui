"""Print job screen. It replaces the dashboard while a job runs, and stays
after the job ends (showing Complete/Cancelled) until dismissed with a click."""

from __future__ import annotations

import asyncio
import math
import time

from ... import protocol as p
from ... import thumbnails
from .. import theme as t
from ..screen import HeaderScreen
from ..widgets import Bar, Icon, Text, Thumbnail, wrap
from .common import fmt_duration

STATE_LABELS = {
    "standby": ("Ready", t.FG),
    "printing": ("Printing", t.OK),
    "paused": ("Paused", t.WARN),
    "complete": ("Complete", t.OK),
    "cancelled": ("Cancelled", t.WARN),
    "error": ("Error", t.ERROR),
}

THUMB = 100
RX = 6 + THUMB + 8                     # right of the thumbnail
COLS = (6, 96, 186)                    # three-column rows
FILAMENT_DIAMETER = 1.75               # when the slicer doesn't say


class PrintingScreen(HeaderScreen):
    title = "Printing"

    def __init__(self, app) -> None:
        super().__init__(app)
        self._metadata: dict = {}
        self._metadata_for: str | None = None

        labels = []

        def label(key, x, y, s, icon=True):
            ref = app.assets.icon("small", key) if icon else None
            if ref:
                labels.append(Icon(f"st.{key}.i", x, y - 2, ref))
                x += ref.w + 4
            labels.append(Text(f"st.{key}.l", x, y, len(s), font=1, fg=t.DIM, value=s))

        T = t.HEADER_H   # layout below the title bar
        name_chars = (p.WIDTH - 6 - RX) // p.FONTS[1][0]
        # Top: thumbnail, and beside it the file name, layer and Z.
        self.thumb = Thumbnail("st.thumb", 6, T + 6, THUMB, t.BG)
        self.file1 = Text("st.file1", RX, T + 6, name_chars, font=1)
        self.file2 = Text("st.file2", RX, T + 22, name_chars, font=1)
        label("layer", RX, T + 50, "Layer", icon=False)
        self.layer = Text("st.layer", p.WIDTH - 6 - 9 * 10, T + 48, 9, font=2, align="right")
        label("zh", RX, T + 80, "Z", icon=False)
        self.z = Text("st.z", p.WIDTH - 6 - 13 * 10, T + 78, 13, font=2, align="right")
        # Progress.
        self.bar = Bar("st.bar", (6, T + 114, p.WIDTH - 6 - 48, T + 127))
        self.percent = Text("st.pct", p.WIDTH - 6 - 5 * 8, T + 113, 5, font=1, align="right")
        # Times.
        label("elapsed", COLS[0], T + 138, "Elapsed")
        label("remaining", COLS[1], T + 138, "Left")
        label("done", COLS[2], T + 138, "Done at", icon=False)
        self.elapsed = Text("st.elapsed", COLS[0], T + 156, 8, font=2)
        self.remaining = Text("st.remaining", COLS[1], T + 156, 8, font=2)
        self.finish = Text("st.finish", COLS[2], T + 156, 8, font=2)
        # Temperatures.
        label("hotend", COLS[0], T + 190, "Hotend")
        label("bed", 140, T + 190, "Bed")
        self.hotend = Text("st.hotend", COLS[0], T + 208, 9, font=3, fg=t.HOT)
        self.bed = Text("st.bed", 140, T + 208, 9, font=3, fg=t.HOT)
        # Live motion.
        label("speed", COLS[0], T + 246, "Speed")
        label("flow", COLS[1], T + 246, "Flow")
        label("fan", COLS[2], T + 246, "Fan")
        self.speed = Text("st.speed", COLS[0], T + 264, 8, font=2)
        self.flow = Text("st.flow", COLS[1], T + 264, 8, font=2)
        self.fan = Text("st.fan", COLS[2], T + 264, 8, font=2)
        # Adjustments and usage.
        label("offset", COLS[0], T + 298, "Offset")
        label("filament", COLS[1], T + 298, "Used")
        label("factors", COLS[2], T + 298, "Spd/Flow", icon=False)
        self.offset = Text("st.offset", COLS[0], T + 316, 8, font=2)
        self.filament = Text("st.filament", COLS[1], T + 316, 8, font=2)
        self.factors = Text("st.factors", COLS[2], T + 316, 8, font=2)
        # M117 / Klipper message, and the hint after the job.
        self.message1 = Text("st.msg1", 6, T + 350, 32, font=1, fg=t.DIM)
        self.message2 = Text("st.msg2", 6, T + 366, 32, font=1, fg=t.DIM)
        self.hint = Text("st.hint", 0, 452, 34, font=1, fg=t.DIM, align="center")
        self.widgets += labels + [
            self.thumb, self.file1, self.file2, self.layer, self.z, self.bar, self.percent,
            self.elapsed, self.remaining, self.finish, self.hotend, self.bed, self.speed, self.flow,
            self.fan, self.offset, self.filament, self.factors, self.message1, self.message2, self.hint,
        ]

    def refresh(self) -> None:
        super().refresh()
        pr = self.printer
        meta = self._metadata
        state = pr.job_state
        label, color = STATE_LABELS.get(state, (state.title(), t.FG))
        self.header_title.value = label
        self.header_title.fg = color

        # Only once the job has ended: a press then opens Home / Reprint.
        self.hint.value = "" if pr.job_active else "Press to continue"
        filename = pr.get("print_stats", "filename", "")
        if filename and filename != self._metadata_for:
            self._metadata_for = filename
            self._metadata = {}
            self.thumb.jpeg = None
            asyncio.ensure_future(self._load_metadata(filename))
        name = (filename.rsplit("/", 1)[-1] or "No file loaded")
        lines = wrap(name, self.file1.chars, 2) + ["", ""]
        self.file1.value, self.file2.value = lines[0], lines[1]

        current, total = self._layers()
        self.layer.value = f"{current or '-'} / {total or '-'}"
        height = meta.get("object_height")
        self.z.value = f"{pr.position(2):.2f}/{height:.2f}" if height else f"{pr.position(2):.2f}"

        progress = pr.progress
        self.bar.value = progress
        self.bar.fg = t.WARN if state == "paused" else t.ACCENT
        self.percent.value = f"{progress * 100:.0f}%"

        elapsed = pr.get("print_stats", "print_duration", 0.0)
        remaining = self._remaining(progress, elapsed) if pr.job_active else None
        self.elapsed.value = fmt_duration(elapsed) if filename else "--"
        self.remaining.value = fmt_duration(remaining)
        self.finish.value = (time.strftime("%H:%M", time.localtime(time.time() + remaining))
                             if remaining is not None else "--")

        for widget, heater, hue in ((self.hotend, "extruder", t.HOT), (self.bed, "heater_bed", t.BED)):
            cur, target = pr.temp(heater)
            widget.value = f"{cur:.0f}/{target:.0f}°"
            widget.fg = hue if target > 0 else t.FG

        speed = float(pr.get("motion_report", "live_velocity", 0.0))
        e_speed = float(pr.get("motion_report", "live_extruder_velocity", 0.0))
        diameter = float(meta.get("filament_diameter") or FILAMENT_DIAMETER)
        flow = max(0.0, e_speed) * math.pi * (diameter / 2) ** 2
        self.speed.value = f"{speed:.0f}mm/s"
        self.flow.value = f"{flow:.0f}mm³/s" if flow < 99.5 else f"{flow:.0f}mm³"
        self.fan.value = f"{pr.get('fan', 'speed', 0.0) * 100:.0f}%"

        self.offset.value = f"{pr.z_offset:+.3f}"
        self.filament.value = f"{pr.get('print_stats', 'filament_used', 0.0) / 1000:.2f}m"
        self.factors.value = (f"{pr.get('gcode_move', 'speed_factor', 1.0) * 100:.0f}/"
                              f"{pr.get('gcode_move', 'extrude_factor', 1.0) * 100:.0f}%")
        message = pr.get("display_status", "message", "") or pr.get("print_stats", "message", "")
        msg = wrap(message, 32, 2) + ["", ""] if message else ["", ""]
        self.message1.value, self.message2.value = msg[0], msg[1]

    def _layers(self) -> tuple[int | None, int | None]:
        """Layer x / y: from the slicer (SET_PRINT_STATS_INFO) if it reports it,
        otherwise worked out from Z and the layer heights, like Mainsail does."""
        info = self.printer.get("print_stats", "info", {}) or {}
        meta = self._metadata
        current, total = info.get("current_layer"), info.get("total_layer")
        lh = meta.get("layer_height")
        first = meta.get("first_layer_height") or lh
        if total is None:
            total = meta.get("layer_count")
            if total is None and lh and meta.get("object_height"):
                total = round((meta["object_height"] - first) / lh) + 1
        state = self.printer.job_state
        if state == "complete" and total:
            current = total
        elif current is None and lh and state != "standby":
            z = self.printer.position(2)          # also the last layer reached after a cancel
            if z > 0:
                current = max(1, math.ceil((z - first) / lh + 1))
                if total:
                    current = min(current, total)
        return current, total

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
        if filename != self._metadata_for:
            return
        self._metadata = meta or {}
        self.app.invalidate()
        rel = thumbnails.pick(self._metadata | {"filename": filename}, THUMB)
        if not rel:
            return
        try:
            png = await self.api.fetch_file("gcodes", rel)
            jpeg = await asyncio.get_running_loop().run_in_executor(
                None, thumbnails.to_jpeg, png, THUMB, p.rgb565_to_rgb(t.BG))
        except Exception:  # noqa: BLE001 - no preview is fine
            return
        if filename == self._metadata_for:
            self.thumb.jpeg = jpeg
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
