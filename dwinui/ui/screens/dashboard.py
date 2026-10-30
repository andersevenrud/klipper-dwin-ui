"""Home screen: every temperature sensor, a heater graph and the toolhead.

Like Mainsail's dashboard. The sensor list comes from Klipper's `heaters`
object, so it adapts to whatever the printer has. Click opens the menu.
"""

from __future__ import annotations

import time

from ... import protocol as p
from ...printer import sensor_label
from .. import theme as t
from ..graph import Graph
from ..screen import HeaderScreen
from ..widgets import Text, Widget
from .printing import STATE_LABELS

TOP = t.HEADER_H + 4
ROW = 20             # font 1 (8x16), 32 columns
MAX_SENSORS = 8
GRAPH_H = 176
# Columns, in characters: name | power/fan % | temperature | target
NAME_W, PCT_W, TEMP_W, TGT_W = 15, 5, 7, 5
VALUES_X = 8 + NAME_W * 8


def series_colors(name: str) -> tuple[int, int]:
    if name.startswith("extruder"):
        return t.HOT, t.HOT_DIM
    if name == "heater_bed":
        return t.BED, t.BED_DIM
    return t.CHAMBER, t.DIM


def shorten(label: str, width: int) -> str:
    """Shorten the longest words first: 'Cartographer MCU' -> 'Carto. MCU'."""
    words = label.split()
    while len(" ".join(words)) > width:
        i = max(range(len(words)), key=lambda k: len(words[k]))
        if len(words[i]) <= 6:
            break
        words[i] = words[i][:5] + "."
    return " ".join(words)[:width]


def values_row(pct: str = "", temp: str = "", target: str = "") -> str:
    return f"{pct:>{PCT_W}}{temp:>{TEMP_W}}{target:>{TGT_W}}"


class DashboardScreen(HeaderScreen):
    title = "Ready"

    def __init__(self, app) -> None:
        super().__init__(app)
        self._base = list(self.widgets)
        self._layout_for: list[str] | None = None
        self.rows: list[tuple[str, Text, Text]] = []
        self.columns = Text("db.cols", VALUES_X, TOP + 2, PCT_W + TEMP_W + TGT_W, font=1, fg=t.DIM,
                            value=values_row("Pwr", "Temp", "Tgt"))
        self.graph: Graph | None = None
        self.position = Text("db.pos", 8, 0, 32, font=1, fg=t.DIM)
        self.hint = Text("db.hint", 0, 452, 34, font=1, fg=t.DIM, align="center", value="press: menu")

    def _layout(self, sensors: list[str]) -> None:
        heaters = self.printer.heater_names
        self.rows = []
        y = TOP + ROW
        colors = {}
        for name in sensors[:MAX_SENSORS]:
            fg = t.FG
            if name in heaters:
                colors[name] = series_colors(name)
                fg = colors[name][0]
            label = Text(f"db.{name}.n", 8, y + 2, NAME_W, font=1, fg=fg,
                         value=shorten(sensor_label(name), NAME_W - 1))
            values = Text(f"db.{name}.v", VALUES_X, y + 2, PCT_W + TEMP_W + TGT_W, font=1)
            self.rows.append((name, label, values))
            y += ROW
        y += 8
        self.graph = Graph("db.graph", (0, y, p.WIDTH - 9, y + GRAPH_H - 1), self.app.history, colors)
        self.position.y = y + GRAPH_H + 8
        widgets: list[Widget] = [self.columns] + [w for _, label, values in self.rows for w in (label, values)]
        self.widgets = self._base + widgets + [self.graph, self.position, self.hint]
        self._layout_for = sensors
        self.app.redraw()

    def refresh(self) -> None:
        super().refresh()
        pr = self.printer
        label, color = STATE_LABELS.get(pr.job_state, (pr.job_state.title(), t.FG))
        self.header_title.value = label if pr.job_state != "standby" else "Ready"
        self.header_title.fg = color
        self.header_temps.value = time.strftime("%H:%M")

        sensors = pr.sensor_names
        if sensors != self._layout_for:
            self._layout(sensors)
        heaters = pr.heater_names
        for name, _, values in self.rows:
            temp = float(pr.get(name, "temperature", 0.0))
            target = float(pr.get(name, "target", 0.0))
            if name in heaters:
                power = float(pr.get(name, "power", 0.0))
                values.value = values_row(f"{power * 100:.0f}%", f"{temp:.1f}°",
                                          f"{target:.0f}°" if target else "off")
            elif name.startswith("temperature_fan"):
                speed = float(pr.get(name, "speed", 0.0))
                values.value = values_row(f"{speed * 100:.0f}%", f"{temp:.1f}°", f"{target:.0f}°")
            else:
                values.value = values_row(temp=f"{temp:.1f}°")

        if "xyz" in pr.homed_axes:
            self.position.value = f"X{pr.position(0):6.1f}  Y{pr.position(1):6.1f}  Z{pr.position(2):6.2f}"
        else:
            homed = pr.homed_axes.upper()
            self.position.value = f"Homed: {homed}" if homed else "Not homed"

    def on_click(self) -> None:
        from .menu import MenuScreen
        self.app.push(MenuScreen(self.app))

    def on_back(self) -> None:
        pass
