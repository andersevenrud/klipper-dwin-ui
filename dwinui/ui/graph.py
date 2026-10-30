"""Scrolling temperature graph.

A full draw happens once (or after a rescale or overlay). After that each new
sample costs a handful of frames: the panel shifts the plot 1 px left (0x09),
then only the new right-hand column is drawn: grid dots, one line segment per
series and a target dot on every other sample (dashed target lines).
"""

from __future__ import annotations

import math
from collections.abc import Iterator

from .. import protocol as p
from ..display import Lazy, Rect
from ..history import TempHistory
from . import theme as t
from .widgets import Part, Widget

GUTTER = 28   # room for y-axis labels (3 chars of font 1)
STEPS = (1, 2, 5, 10, 20, 25, 50, 100)
MIN_SPAN = 10.0


def nice_range(lo: float, hi: float) -> tuple[int, int, int]:
    """(ymin, ymax, grid step) covering lo..hi with about 3-5 grid lines.
    Flat data gets at least MIN_SPAN degrees, centred on it."""
    if hi - lo < MIN_SPAN:
        mid = (lo + hi) / 2
        lo, hi = mid - MIN_SPAN / 2, mid + MIN_SPAN / 2
    span = hi - lo
    step = next((s for s in STEPS if span / s <= 5), STEPS[-1])
    ymin = max(0, math.floor((lo - span * 0.05) / step) * step)
    ymax = math.ceil((hi + span * 0.05) / step) * step
    return ymin, max(ymax, ymin + step), step


class Graph(Widget):
    def __init__(self, key: str, area: Rect, history: TempHistory,
                 colors: dict[str, tuple[int, int]]) -> None:
        """colors: series name -> (line colour, target colour)."""
        self.key, self.area, self.history, self.colors = key, area, history, colors
        x1, y1, x2, y2 = area
        self.plot = (x1 + GUTTER, y1, x2, y2)
        self._range: tuple[int, int, int] | None = None
        self._drawn = 0
        self._last_y: dict[str, int] = {}
        self._rebuilds = 0

    @property
    def width(self) -> int:
        return self.plot[2] - self.plot[0] + 1

    def _series(self) -> list[str]:
        # Draw order: later series end up on top; keep the hotend on top.
        return [n for n in reversed(list(self.colors)) if n in self.history.data]

    def _scale(self) -> tuple[int, int, int]:
        """Fit the visible data (and active targets). Keep the current range
        while the data still fits it reasonably, so rescales (full redraws)
        stay rare."""
        values = [v for name in self._series()
                  for temp, target in list(self.history.data[name])[-self.width:]
                  for v in ((temp, target) if target > 0 else (temp,))]
        if not values:
            return self._range or (0, 100, 50)
        lo, hi = min(values), max(values)
        if self._range:
            ymin, ymax, _ = self._range
            if ymin <= lo and hi <= ymax and max(hi - lo, MIN_SPAN) >= 0.5 * (ymax - ymin):
                return self._range
        return nice_range(lo, hi)

    def _grid(self) -> list[int]:
        ymin, ymax, step = self._range
        return list(range(ymin, ymax + 1, step))

    def _y(self, value: float) -> int:
        _, y1, _, y2 = self.plot
        ymin, ymax, _ = self._range
        value = max(ymin, min(value, ymax))
        return y2 - round((value - ymin) / (ymax - ymin) * (y2 - y1))

    def parts(self) -> Iterator[Part]:
        self._range = self._scale()
        sig = (self.history.generation, self._range, self._rebuilds, tuple(self._series()))
        yield self.key, self.area, Lazy(sig, self._build)

    def _build(self) -> list[bytes]:
        x1, y1, x2, y2 = self.area
        px1, py1, px2, py2 = self.plot
        frames = [p.fill(t.BG, *self.area), p.fill(t.GRAPH_BG, *self.plot)]
        cw, ch = p.FONTS[1]
        for value in self._grid():
            y = self._y(value)
            frames.append(p.line(t.GRID, px1, y, px2, y))
            ly = max(y1, min(y2 - ch + 1, y - ch // 2))
            frames.append(p.text(x1, ly, f"{value:>3}", 1, t.DIM, t.BG))
        for name in self._series():
            samples = list(self.history.data[name])[-self.width:]
            color, target_color = self.colors[name]
            x0 = px2 - len(samples) + 1
            first = self.history.count - len(samples)
            dots = [(x0 + i, self._y(tg)) for i, (_, tg) in enumerate(samples)
                    if tg > 0 and (first + i) % 2 == 0]
            if dots:
                frames.append(p.points(target_color, dots))
            frames += _polyline(color, [(x0 + i, self._y(v)) for i, (v, _) in enumerate(samples)])
            if samples:
                self._last_y[name] = self._y(samples[-1][0])
        self._drawn = self.history.count
        return frames

    def incremental(self, drew_full: bool) -> list[bytes]:
        """Frames for samples that arrived since the last draw."""
        new = self.history.count - self._drawn
        if drew_full or new <= 0:
            return []
        if new > 8:
            self._rebuilds += 1          # fell far behind: redraw everything next pass
            return []
        px1, py1, px2, py2 = self.plot
        grid = [(px2, self._y(v)) for v in self._grid()]
        frames = []
        for k in range(new, 0, -1):
            frames.append(p.area_move(p.MOVE_LEFT, 1, t.GRAPH_BG, *self.plot))
            frames.append(p.points(t.GRID, grid))
            index = self.history.count - k
            for name in self._series():
                series = self.history.data[name]
                if len(series) < k:
                    continue
                value, target = series[-k]
                color, target_color = self.colors[name]
                if target > 0 and index % 2 == 0:
                    frames.append(p.points(target_color, [(px2, self._y(target))]))
                y = self._y(value)
                frames.append(p.line(color, px2 - 1, self._last_y.get(name, y), px2, y))
                self._last_y[name] = y
        self._drawn = self.history.count
        return frames


def _polyline(color: int, pts: list[tuple[int, int]]) -> list[bytes]:
    """Line segments, merging flat runs into one horizontal line each."""
    if not pts:
        return []
    frames = []
    sx, sy = px, py = pts[0]
    for x, y in pts[1:]:
        if y == py:
            px = x
            continue
        frames.append(p.line(color, sx, sy, px, py))
        frames.append(p.line(color, px, py, x, y))
        sx, sy = px, py = x, y
    frames.append(p.line(color, sx, sy, px, py))
    return frames
