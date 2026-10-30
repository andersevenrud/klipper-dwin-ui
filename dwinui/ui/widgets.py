"""Widgets: each yields (key, rect, frames) parts for the display cache.

Widgets are plain value holders; screens set their values in refresh() and
the Display skips any part whose frames did not change.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import hashlib

from .. import protocol as p
from ..assets import IconRef
from ..display import Lazy, Rect
from . import theme as t

Part = tuple[str, Rect, list[bytes]]


def fit(s: str, chars: int, align: str = "left") -> str:
    s = s[:chars]
    if align == "right":
        return s.rjust(chars)
    if align == "center":
        return s.center(chars)
    return s.ljust(chars)


def wrap(s: str, chars: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    for para in s.splitlines() or [""]:
        line = ""
        for word in para.split():
            while len(word) > chars:
                if line:
                    lines.append(line)
                    line = ""
                lines.append(word[:chars])
                word = word[chars:]
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= chars:
                line += " " + word
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines[:max_lines]


class Widget:
    def parts(self) -> Iterator[Part]:
        raise NotImplementedError


class Box(Widget):
    def __init__(self, key: str, area: Rect, color: int, mode: int = p.RECT_FILL) -> None:
        self.key, self.area, self.color, self.mode = key, area, color, mode

    def parts(self) -> Iterator[Part]:
        yield self.key, self.area, [p.rect(self.mode, self.color, *self.area)]


class Icon(Widget):
    """Icon from the flashed library; draws nothing without custom assets."""

    def __init__(self, key: str, x: int, y: int, ref: IconRef | None) -> None:
        self.key, self.x, self.y, self.ref = key, x, y, ref

    def parts(self) -> Iterator[Part]:
        if self.ref:
            r = self.ref
            yield self.key, (self.x, self.y, self.x + r.w - 1, self.y + r.h - 1), [
                p.icon(r.lib, r.id, self.x, self.y)]


class Thumbnail(Widget):
    """A JPEG uploaded to the panel's SRAM and drawn from there, or a text
    placeholder while there is none."""

    def __init__(self, key: str, x: int, y: int, size: int, bg: int) -> None:
        self.key, self.x, self.y, self.size, self.bg = key, x, y, size, bg
        self.area = (x, y, x + size - 1, y + size - 1)
        self.jpeg: bytes | None = None
        self.placeholder = ""

    def parts(self) -> Iterator[Part]:
        if self.jpeg:
            jpeg = self.jpeg
            sig = hashlib.sha1(jpeg).hexdigest()
            yield self.key, self.area, Lazy(sig, lambda: [p.sram_write(0, jpeg),
                                                           p.sram_icon(self.x, self.y, 0)])
            return
        cw, ch = p.FONTS[1]
        text = self.placeholder[:self.size // cw]
        yield self.key, self.area, [
            p.fill(t.BG, *self.area),
            p.text(self.x + (self.size - len(text) * cw) // 2, self.y + (self.size - ch) // 2,
                   text, 1, t.DIM, t.BG),
        ]


class Text(Widget):
    """Fixed-width text field; padding to `chars` erases the previous value."""

    def __init__(self, key: str, x: int, y: int, chars: int, font: int = 2,
                 fg: int = t.FG, bg: int = t.BG, align: str = "left", value: str = "") -> None:
        self.key, self.x, self.y, self.chars, self.font = key, x, y, chars, font
        self.fg, self.bg, self.align, self.value = fg, bg, align, value

    @property
    def area(self) -> Rect:
        cw, ch = p.FONTS[self.font]
        return (self.x, self.y, self.x + self.chars * cw - 1, self.y + ch - 1)

    def parts(self) -> Iterator[Part]:
        s = fit(self.value, self.chars, self.align)
        yield self.key, self.area, [p.text(self.x, self.y, s, self.font, self.fg, self.bg)]


class Bar(Widget):
    def __init__(self, key: str, area: Rect, fg: int = t.ACCENT, bg: int = t.BAR_BG) -> None:
        self.key, self.area, self.fg, self.bg = key, area, fg, bg
        self.value = 0.0

    def parts(self) -> Iterator[Part]:
        x1, y1, x2, y2 = self.area
        split = x1 + round((x2 - x1 + 1) * max(0.0, min(1.0, self.value)))
        frames = []
        if split > x1:
            frames.append(p.fill(self.fg, x1, y1, split - 1, y2))
        if split <= x2:
            frames.append(p.fill(self.bg, split, y1, x2, y2))
        yield self.key, self.area, frames


class Tile(Widget):
    def __init__(self, key: str, area: Rect) -> None:
        self.key, self.area = key, area
        self.label = ""
        self.sub = ""
        self.selected = False
        self.enabled = True
        self.icon: IconRef | None = None       # baked on TILE_BG
        self.icon_sel: IconRef | None = None   # baked on ACCENT

    def parts(self) -> Iterator[Part]:
        x1, y1, x2, y2 = self.area
        bg = t.ACCENT if self.selected else t.TILE_BG
        fg = t.FG if self.enabled else t.DISABLED
        w, h = x2 - x1 + 1, y2 - y1 + 1
        frames = [p.fill(bg, *self.area)]
        icon = self.icon_sel if self.selected else self.icon
        if icon and self.label:
            cw, ch = p.FONTS[2]
            scw, sch = p.FONTS[1]
            block = icon.h + 4 + ch + (2 + sch if self.sub else 0)
            iy = y1 + (h - block) // 2
            frames.append(p.icon(icon.lib, icon.id, x1 + (w - icon.w) // 2, iy))
            label = self.label[:w // cw]
            ly = iy + icon.h + 4
            frames.append(p.text(x1 + (w - len(label) * cw) // 2, ly, label, 2, fg, bg))
            if self.sub:
                sub = self.sub[:w // scw]
                frames.append(p.text(x1 + (w - len(sub) * scw) // 2, ly + ch + 2, sub, 1,
                                     t.FG if self.selected else t.DIM, bg))
        elif self.label:
            cw, ch = p.FONTS[3]
            chars = w // cw
            label = self.label[:chars]
            ly = y1 + h // 2 - ch + (0 if self.sub else ch // 2)
            frames.append(p.text(x1 + (w - len(label) * cw) // 2, ly, label, 3, fg, bg))
            if self.sub:
                scw, _ = p.FONTS[1]
                sub = self.sub[:w // scw]
                frames.append(p.text(x1 + (w - len(sub) * scw) // 2, y1 + h // 2 + 6, sub, 1,
                                     t.FG if self.selected else t.DIM, bg))
        yield self.key, self.area, frames


class ScrollBar(Widget):
    """Thin vertical position indicator; blank when everything fits."""

    def __init__(self, key: str, area: Rect) -> None:
        self.key, self.area = key, area
        self.total = self.visible = self.top = 0

    def parts(self) -> Iterator[Part]:
        x1, y1, x2, y2 = self.area
        if self.total <= self.visible:
            yield self.key, self.area, [p.fill(t.BG, *self.area)]
            return
        span = y2 - y1 + 1
        thumb = max(8, span * self.visible // self.total)
        ty = y1 + (span - thumb) * self.top // (self.total - self.visible)
        yield self.key, self.area, [p.fill(t.BAR_BG, *self.area),
                                    p.fill(t.DIM, x1, ty, x2, ty + thumb - 1)]


@dataclass
class Row:
    label: str
    value: str = ""
    enabled: bool = True
    editing: bool = False


@dataclass
class ListView(Widget):
    key: str
    area: Rect
    row_h: int = 38
    font: int = 2
    rows: list[Row] = field(default_factory=list)
    selected: int = 0
    top: int = 0

    @property
    def visible(self) -> int:
        return (self.area[3] - self.area[1] + 1) // self.row_h

    def scroll_to_selection(self) -> None:
        if self.selected < self.top:
            self.top = self.selected
        elif self.selected >= self.top + self.visible:
            self.top = self.selected - self.visible + 1
        self.top = max(0, min(self.top, max(0, len(self.rows) - self.visible)))

    def parts(self) -> Iterator[Part]:
        x1, y1, x2, _ = self.area
        cw, ch = p.FONTS[self.font]
        has_sb = len(self.rows) > self.visible
        right = x2 - (6 if has_sb else 0)
        chars = (right - x1 + 1 - 12) // cw
        for i in range(self.visible):
            idx = self.top + i
            ry = y1 + i * self.row_h
            area = (x1, ry, right, ry + self.row_h - 1)
            key = f"{self.key}.{i}"
            if idx >= len(self.rows):
                yield key, area, [p.fill(t.BG, *area)]
                continue
            row = self.rows[idx]
            sel = idx == self.selected
            bg = (t.EDIT if row.editing else t.ACCENT) if sel else t.BG
            fg = t.FG if row.enabled else t.DISABLED
            value = row.value[:chars - 2]
            room = chars - len(value) - (1 if value else 0)
            s = fit(row.label, room) + (" " + value if value else "")
            frames = [p.fill(bg, *area),
                      p.text(x1 + 6, ry + (self.row_h - ch) // 2, s, self.font, fg, bg)]
            if not sel and i + 1 < self.visible:
                frames.append(p.line(t.HEADER_BG, x1 + 6, ry + self.row_h - 1,
                                     right - 6, ry + self.row_h - 1))
            yield key, area, frames
        sb_area = (x2 - 3, y1, x2, y1 + self.visible * self.row_h - 1)
        if not has_sb:
            yield f"{self.key}.sb", sb_area, [p.fill(t.BG, *sb_area)]
        else:
            span = sb_area[3] - sb_area[1] + 1
            thumb_h = max(12, span * self.visible // len(self.rows))
            thumb_y = sb_area[1] + (span - thumb_h) * self.top // max(1, len(self.rows) - self.visible)
            yield f"{self.key}.sb", sb_area, [
                p.fill(t.BAR_BG, *sb_area),
                p.fill(t.DIM, sb_area[0], thumb_y, sb_area[2], thumb_y + thumb_h - 1),
            ]
