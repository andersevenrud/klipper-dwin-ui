"""Home screen: every temperature sensor, a heater graph and the toolhead.

Like Mainsail's dashboard. The sensor list comes from Klipper's `heaters`
object, so it adapts to whatever the printer has. Hotend and bed stay pinned
on top; when there are more sensors than `dashboard_rows`, turning the knob
scrolls the rest. Click opens the menu.
"""

from __future__ import annotations

import time

from ... import protocol as p
from ...printer import sensor_label
from .. import theme as t
from ..graph import Graph
from ..screen import HeaderScreen
from ..widgets import ScrollBar, Text, Widget
from .printing import STATE_LABELS

TOP = t.HEADER_H + 4
ROW = 20             # font 1 (8x16), 32 columns
GRAPH_BOTTOM = 410   # the graph takes whatever the rows leave above this
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
        self.slots: list[tuple[Text, Text]] = []     # row widgets, reused while scrolling
        self.pinned: list[str] = []                  # hotend(s) and bed
        self.rest: list[str] = []                    # everything else, scrollable
        self.scroll = 0
        self.scrollbar: ScrollBar | None = None
        self.columns = Text("db.cols", VALUES_X, TOP + 2, PCT_W + TEMP_W + TGT_W, font=1, fg=t.DIM,
                            value=values_row("Pwr", "Temp", "Tgt"))
        self.graph: Graph | None = None
        self.position = Text("db.pos", 8, 0, 32, font=1, fg=t.DIM)
        self.hint = Text("db.hint", 0, 452, 34, font=1, fg=t.DIM, align="center")

    @property
    def scroll_slots(self) -> int:
        return len(self.slots) - len(self.pinned)

    def _layout(self, sensors: list[str]) -> None:
        heaters = self.printer.heater_names
        rows = max(1, min(len(sensors), self.cfg.dashboard_rows))
        self.pinned = [n for n in sensors if n.startswith("extruder") or n == "heater_bed"][:rows]
        self.rest = [n for n in sensors if n not in self.pinned]
        self.scroll = 0
        self.slots = []
        y = TOP + ROW
        for i in range(rows):
            label = Text(f"db.row{i}.n", 8, y + 2, NAME_W, font=1)
            values = Text(f"db.row{i}.v", VALUES_X, y + 2, PCT_W + TEMP_W + TGT_W, font=1)
            self.slots.append((label, values))
            y += ROW
        first = TOP + ROW * (1 + len(self.pinned))
        self.scrollbar = ScrollBar("db.scroll", (p.WIDTH - 5, first + 2, p.WIDTH - 3, y - 3))
        y += 8
        colors = {n: series_colors(n) for n in heaters}
        self.graph = Graph("db.graph", (0, y, p.WIDTH - 9, GRAPH_BOTTOM), self.app.history, colors)
        self.position.y = GRAPH_BOTTOM + 6
        widgets: list[Widget] = [self.columns] + [w for slot in self.slots for w in slot]
        self.widgets = self._base + widgets + [self.scrollbar, self.graph, self.position, self.hint]
        self._layout_for = sensors
        self.app.redraw()

    def visible_sensors(self) -> list[str]:
        return self.pinned + self.rest[self.scroll:self.scroll + self.scroll_slots]

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
        shown = self.visible_sensors()
        for i, (label, values) in enumerate(self.slots):
            if i >= len(shown):
                label.value = values.value = ""
                continue
            name = shown[i]
            label.value = shorten(sensor_label(name), NAME_W - 1)
            label.fg = series_colors(name)[0] if name in heaters else t.FG
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
        if self.scrollbar:
            self.scrollbar.total = len(self.rest)
            self.scrollbar.visible = self.scroll_slots
            self.scrollbar.top = self.scroll
        scrollable = len(self.rest) > self.scroll_slots
        self.hint.value = "turn: scroll  press: menu" if scrollable else "press: menu"

        if "xyz" in pr.homed_axes:
            self.position.value = f"X{pr.position(0):6.1f}  Y{pr.position(1):6.1f}  Z{pr.position(2):6.2f}"
        else:
            homed = pr.homed_axes.upper()
            self.position.value = f"Homed: {homed}" if homed else "Not homed"

    def on_rotate(self, delta: int) -> None:
        most = max(0, len(self.rest) - self.scroll_slots)
        self.scroll = max(0, min(most, self.scroll + delta))

    def on_click(self) -> None:
        from .menu import MenuScreen
        self.app.push(MenuScreen(self.app))

    def on_back(self) -> None:
        pass
