"""G-code file browser with a preview dialog (thumbnail + slicer details)."""

from __future__ import annotations

import asyncio
import logging
import time

from ... import protocol as p
from ... import thumbnails
from .. import theme as t
from ..screen import ButtonWidget, Item, ListScreen, Screen
from ..widgets import Box, Text, Thumbnail
from .common import fmt_duration

log = logging.getLogger(__name__)

THUMB = 200


def fmt_age(mtime: float) -> str:
    age = max(0, time.time() - mtime)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if age >= size:
            return f"{age // size:.0f}{unit}"
    return "now"


class FilesScreen(ListScreen):
    title = "Files"

    def on_enter(self) -> None:
        self.empty_text = "Loading..."
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        try:
            files = await self.api.call("server.files.list", root="gcodes")
        except Exception as e:  # noqa: BLE001
            self.empty_text = "Could not list files"
            self.app.toast(str(e), "error")
            return
        files = sorted(files or [], key=lambda f: f.get("modified", 0), reverse=True)
        self.set_items([
            Item(f["path"], value=lambda m=f.get("modified", 0): fmt_age(m),
                 action=lambda path=f["path"]: asyncio.ensure_future(self._preview(path)))
            for f in files
        ])
        self.empty_text = "No G-code files"
        self.app.invalidate()

    async def _preview(self, path: str) -> None:
        try:
            meta = await self.api.call("server.files.metadata", filename=path)
        except Exception:  # noqa: BLE001 - metadata is optional (no slicer info)
            meta = {}
        self.app.push(PrintPreview(self.app, path, meta or {}))


class PrintPreview(Screen):
    """Large dialog: file name, thumbnail, slicer details, Cancel/Print."""

    modal = True
    area = (6, t.HEADER_H + 6, p.WIDTH - 7, p.HEIGHT - 8)

    def __init__(self, app, path: str, meta: dict) -> None:
        super().__init__(app)
        self.path = path
        self.meta = meta
        self.choice = 1
        x1, y1, x2, y2 = self.area
        w = x2 - x1 + 1
        self.widgets += [Box("dlg.bg", self.area, t.TILE_BG), Box("dlg.border", self.area, t.ACCENT, p.RECT_FRAME)]
        name = path.rsplit("/", 1)[-1]
        name_chars = (w - 16) // p.FONTS[2][0]
        self.widgets.append(Text("dlg.title", x1 + 8, y1 + 10, name_chars, font=2, bg=t.TILE_BG,
                                 value=name if len(name) <= name_chars else name[:name_chars - 1] + "…"))
        self.thumb = Thumbnail("dlg.thumb", x1 + (w - THUMB) // 2, y1 + 40, THUMB, t.TILE_BG)
        self.thumb.placeholder = "Loading preview…" if thumbnails.pick(meta, THUMB) else "No preview"
        self.widgets.append(self.thumb)
        y = y1 + 40 + THUMB + 12
        for i, line in enumerate(self._details()):
            self.widgets.append(Text(f"dlg.info{i}", x1 + 8, y, 30, font=1, fg=t.DIM, bg=t.TILE_BG, value=line))
            y += 20
        bw = (w - 36) // 2
        by = y2 - 52
        self.buttons = [
            ButtonWidget("dlg.b0", (x1 + 12, by, x1 + 12 + bw, by + 39), "Cancel", t.DISABLED),
            ButtonWidget("dlg.b1", (x2 - 12 - bw, by, x2 - 12, by + 39), "Print", t.ACCENT),
        ]
        self.widgets += self.buttons

    def _details(self) -> list[str]:
        m = self.meta
        cells = []
        if m.get("estimated_time"):
            cells.append(f"Time {fmt_duration(m['estimated_time'])}")
        if m.get("filament_total"):
            cells.append(f"Filament {m['filament_total'] / 1000:.2f}m")
        if m.get("layer_height"):
            cells.append(f"Layer {m['layer_height']:g}mm")
        if m.get("object_height"):
            cells.append(f"Height {m['object_height']:g}mm")
        lines = [f"{a:<15}{b}" for a, b in zip(cells[::2], cells[1::2] + [""])]
        if m.get("slicer"):
            lines.append(f"{m['slicer']} {m.get('slicer_version', '')}".strip()[:30])
        return lines[:4]

    def on_enter(self) -> None:
        asyncio.ensure_future(self._load_thumbnail())

    async def _load_thumbnail(self) -> None:
        rel = thumbnails.pick(self.meta | {"filename": self.path}, THUMB)
        if not rel:
            return
        try:
            png = await self.api.fetch_file("gcodes", rel)
            jpeg = await asyncio.get_running_loop().run_in_executor(
                None, thumbnails.to_jpeg, png, THUMB, p.rgb565_to_rgb(t.TILE_BG))
        except Exception as e:  # noqa: BLE001 - a missing preview is not an error worth a toast
            log.info("no thumbnail for %s: %s", self.path, e)
            self.thumb.placeholder = "No preview"
        else:
            self.thumb.jpeg = jpeg
        self.app.invalidate()

    def refresh(self) -> None:
        for i, b in enumerate(self.buttons):
            b.selected = i == self.choice

    def on_rotate(self, delta: int) -> None:
        self.choice = 1 if delta > 0 else 0

    def on_click(self) -> None:
        self.app.pop()
        if self.choice == 1:
            self.api.fire("printer.print.start", filename=self.path)
            self.app.pop_to_root()
