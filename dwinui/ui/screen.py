"""Screen base classes, list menus with inline editors, and dialogs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .. import protocol as p
from ..display import Rect
from . import theme as t
from .widgets import Box, ListView, Row, Text, Widget, fit, wrap

if TYPE_CHECKING:
    from .app import App


class Screen:
    title = ""
    modal = False
    area: Rect = (0, 0, p.WIDTH - 1, p.HEIGHT - 1)

    def __init__(self, app: App) -> None:
        self.app = app
        self.widgets: list[Widget] = []

    @property
    def printer(self):
        return self.app.printer

    @property
    def api(self):
        return self.app.api

    @property
    def cfg(self):
        return self.app.cfg

    def on_enter(self) -> None: ...
    def on_exit(self) -> None: ...
    def refresh(self) -> None: ...
    def on_rotate(self, delta: int) -> None: ...
    def on_click(self) -> None: ...

    def on_back(self) -> None:
        self.app.pop()


class HeaderScreen(Screen):
    """Screen with a title bar showing hotend/bed temperatures on the right."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.header_bg = Box("hdr.bg", (0, 0, p.WIDTH - 1, t.HEADER_H - 1), t.HEADER_BG)
        self.header_title = Text("hdr.title", 8, 6, 13, font=3, bg=t.HEADER_BG, value=self.title)
        self.header_temps = Text("hdr.temps", p.WIDTH - 8 - 11 * 8, 10, 11, font=1, fg=t.DIM,
                                 bg=t.HEADER_BG, align="right")
        self.widgets += [self.header_bg, self.header_title, self.header_temps]

    def refresh(self) -> None:
        e, _ = self.printer.temp("extruder")
        b, _ = self.printer.temp("heater_bed")
        self.header_temps.value = f"E{e:.0f} B{b:.0f}" if self.printer.ready else ""


# -- list menus --------------------------------------------------------------

class NumberEdit:
    """Inline editor: rotate changes a pending value, click commits."""

    def __init__(self, get: Callable[[], float], commit: Callable[[float], None],
                 lo: float, hi: float, step: float = 1, fmt: str = "{:.0f}") -> None:
        self.get, self.commit, self.lo, self.hi, self.step, self.fmt = get, commit, lo, hi, step, fmt
        self.pending = 0.0

    def begin(self) -> None:
        self.pending = self.get()

    def rotate(self, delta: int) -> None:
        # Bigger jumps when spun fast (the bridge sends accumulated deltas).
        mult = 5 if abs(delta) >= 3 else 1
        self.pending = max(self.lo, min(self.hi, self.pending + delta * mult * self.step))

    def text(self) -> str:
        return "> " + self.fmt.format(self.pending)

    def finish(self) -> None:
        self.commit(self.pending)


class Adjust:
    """Live editor: every detent acts immediately (jog, babystep)."""

    def __init__(self, on_delta: Callable[[int], None], text: Callable[[], str]) -> None:
        self.on_delta, self._text = on_delta, text

    def begin(self) -> None: ...

    def rotate(self, delta: int) -> None:
        self.on_delta(delta)

    def text(self) -> str:
        return "<> " + self._text()

    def finish(self) -> None: ...


@dataclass
class Item:
    label: str | Callable[[], str]
    value: Callable[[], str] | None = None
    action: Callable[[], None] | None = None
    edit: NumberEdit | Adjust | None = None
    enabled: Callable[[], bool] | None = None

    def is_enabled(self) -> bool:
        return self.enabled() if self.enabled else True


class ListScreen(HeaderScreen):
    back_row = True   # visible "< Back" entry on top; long press works too

    def __init__(self, app: App, items: list[Item] | None = None) -> None:
        super().__init__(app)
        self.list = ListView("list", (0, t.HEADER_H + 4, p.WIDTH - 1, p.HEIGHT - 50))
        self.editing: Item | None = None
        self.empty_text = ""
        self._moved = False
        self.widgets.append(self.list)
        self.set_items(items or [])

    def set_items(self, items: list[Item]) -> None:
        head = [Item("< Back", action=self.app.pop)] if self.back_row else []
        self.items = head + items
        if not self._moved:
            # Start on the first real entry; Back is one step up.
            self.list.selected = len(head) if items else 0
        self.list.selected = min(self.list.selected, max(0, len(self.items) - 1))
        self.editing = None

    def refresh(self) -> None:
        super().refresh()
        rows = []
        for item in self.items:
            label = item.label() if callable(item.label) else item.label
            editing = item is self.editing and item.edit is not None
            value = item.edit.text() if editing else (item.value() if item.value else "")
            rows.append(Row(label, value, item.is_enabled(), editing))
        if self.empty_text and len(self.items) == (1 if self.back_row else 0):
            rows.append(Row(self.empty_text, enabled=False))
        self.list.rows = rows
        self.list.scroll_to_selection()

    @property
    def current(self) -> Item | None:
        if 0 <= self.list.selected < len(self.items):
            return self.items[self.list.selected]
        return None

    def on_rotate(self, delta: int) -> None:
        if self.editing and self.editing.edit:
            self.editing.edit.rotate(delta)
            return
        if self.items:
            self._moved = True
            self.list.selected = max(0, min(len(self.items) - 1, self.list.selected + delta))

    def on_click(self) -> None:
        item = self.current
        if item is None:
            return
        if self.editing is item and item.edit:
            self.editing = None
            item.edit.finish()
            return
        if not item.is_enabled():
            self.app.toast("Not available now")
            return
        if item.edit:
            item.edit.begin()
            self.editing = item
        elif item.action:
            item.action()

    def on_back(self) -> None:
        if self.editing:
            self.editing = None   # cancel without committing
            return
        super().on_back()


# -- dialogs -------------------------------------------------------------------

class Confirm(Screen):
    modal = True
    area = (12, 96, p.WIDTH - 13, 383)

    def __init__(self, app: App, title: str, message: str, on_ok: Callable[[], None],
                 ok: str = "OK", cancel: str = "Cancel", danger: bool = False,
                 default_ok: bool = False) -> None:
        super().__init__(app)
        x1, y1, x2, y2 = self.area
        self.on_ok = on_ok
        self.choice = 1 if default_ok else 0
        accent = t.ERROR if danger else t.ACCENT
        self.widgets.append(Box("dlg.bg", self.area, t.TILE_BG))
        self.widgets.append(Box("dlg.border", self.area, accent, mode=p.RECT_FRAME))
        self.widgets.append(Text("dlg.title", x1 + 12, y1 + 12, 18, font=3, bg=t.TILE_BG,
                                 fg=t.FG, value=title))
        lines = wrap(message, 28, 8)
        for i, line in enumerate(lines):
            self.widgets.append(Text(f"dlg.l{i}", x1 + 12, y1 + 50 + i * 22, 28, font=1,
                                     bg=t.TILE_BG, fg=t.DIM, value=line))
        bw = (x2 - x1 - 36) // 2
        by = y2 - 56
        self.buttons = [
            ButtonWidget("dlg.b0", (x1 + 12, by, x1 + 12 + bw, by + 43), cancel, t.ACCENT),
            ButtonWidget("dlg.b1", (x2 - 12 - bw, by, x2 - 12, by + 43), ok, accent),
        ]
        self.widgets += self.buttons

    def refresh(self) -> None:
        for i, b in enumerate(self.buttons):
            b.selected = i == self.choice

    def on_rotate(self, delta: int) -> None:
        self.choice = 1 if delta > 0 else 0

    def on_click(self) -> None:
        self.app.pop()
        if self.choice == 1:
            self.on_ok()


class ButtonWidget(Widget):
    def __init__(self, key: str, area: Rect, label: str, accent: int) -> None:
        self.key, self.area, self.label, self.accent = key, area, label, accent
        self.selected = False

    def parts(self):
        x1, y1, x2, y2 = self.area
        bg = self.accent if self.selected else t.HEADER_BG
        cw, ch = p.FONTS[2]
        chars = (x2 - x1 + 1) // cw
        s = fit(self.label, chars, "center").strip()
        tx = x1 + (x2 - x1 + 1 - len(s) * cw) // 2
        yield self.key, self.area, [p.fill(bg, *self.area),
                                    p.text(tx, y1 + (y2 - y1 + 1 - ch) // 2, s, 2, t.FG, bg)]
