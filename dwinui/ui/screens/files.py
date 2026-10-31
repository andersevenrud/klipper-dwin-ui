"""G-code file browser with a preview dialog (thumbnail + slicer details)."""

from __future__ import annotations

import asyncio
import logging
import posixpath
import time
from collections.abc import Callable

from ... import protocol as p
from ... import thumbnails
from .. import theme as t
from ..screen import ButtonWidget, Confirm, Item, ListScreen, Screen
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


RESULTS = {"completed": "✓", "cancelled": "×"}       # anything else ended badly: "!"


class FilesScreen(ListScreen):
    """A folder of the gcodes root: Recent prints (top level only), folders,
    then files newest first. Folders open as their own screen."""

    title = "Print"

    def __init__(self, app, folder: str = "") -> None:
        self.folder = folder                         # relative to gcodes, "" = top
        super().__init__(app)
        if folder:
            self.header_title.value = posixpath.basename(folder)[:20]

    def on_enter(self) -> None:
        self.empty_text = "Loading..."
        self.reload()

    def reload(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        path = posixpath.join("gcodes", self.folder) if self.folder else "gcodes"
        try:
            listing = await self.api.call("server.files.get_directory", path=path, extended=False)
        except Exception as e:  # noqa: BLE001
            self.empty_text = "Could not list files"
            self.app.toast(str(e), "error")
            self.app.invalidate()
            return
        dirs = sorted((d["dirname"] for d in listing.get("dirs", []) if not d["dirname"].startswith(".")),
                      key=str.lower)
        files = sorted(listing.get("files", []), key=lambda f: f.get("modified", 0), reverse=True)
        items = []
        if not self.folder and self.cfg.recent_prints > 0:
            items.append(Item("↻ Recent prints", action=lambda: self.app.push(RecentScreen(self.app))))
        for d in dirs:
            sub = posixpath.join(self.folder, d)
            items.append(Item(f"{d}/", action=lambda sub=sub: self.app.push(FilesScreen(self.app, sub))))
        for f in files:
            full = posixpath.join(self.folder, f["filename"])
            items.append(Item(f["filename"], value=lambda m=f.get("modified", 0): fmt_age(m),
                              action=lambda p=full: asyncio.ensure_future(open_preview(self, p))))
        self.set_items(items)
        self.empty_text = "Empty folder" if self.folder else "No G-code files"
        self.app.invalidate()


class RecentScreen(ListScreen):
    """The last few distinct files from Moonraker's print history, with how
    their latest print ended."""

    title = "Recent prints"

    def on_enter(self) -> None:
        self.empty_text = "Loading..."
        self.reload()

    def reload(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        wanted = self.cfg.recent_prints
        try:
            # Ask for more jobs than files wanted: the same file is often printed
            # several times in a row.
            res = await self.api.call("server.history.list", limit=max(50, wanted * 5), order="desc")
        except Exception as e:  # noqa: BLE001 - no history component, or an old Moonraker
            self.empty_text = "No print history"
            log.info("print history unavailable: %s", e)
            self.app.invalidate()
            return
        seen: dict[str, dict] = {}
        for job in res.get("jobs", []):
            name = job.get("filename")
            if name and name not in seen and job.get("status") != "in_progress":
                seen[name] = job
            if len(seen) >= wanted:
                break
        items = []
        for name, job in seen.items():
            mark = RESULTS.get(job.get("status"), "!")
            ended = job.get("end_time") or job.get("start_time") or 0
            exists = job.get("exists", True)
            items.append(Item(name, value=lambda m=mark, e=ended: f"{m} {fmt_age(e)}",
                              action=lambda p=name: asyncio.ensure_future(open_preview(self, p)),
                              enabled=lambda x=exists: x))
        self.set_items(items)
        self.empty_text = "No prints yet"
        self.app.invalidate()


async def open_preview(screen: ListScreen, path: str) -> None:
    try:
        meta = await screen.api.call("server.files.metadata", filename=path)
    except Exception:  # noqa: BLE001 - metadata is optional (no slicer info)
        meta = {}
    screen.app.push(PrintPreview(screen.app, path, meta or {}, on_deleted=screen.reload))


class PrintPreview(Screen):
    """Large dialog: file name, thumbnail, slicer details, Cancel/Print."""

    modal = True
    area = (6, t.HEADER_H + 6, p.WIDTH - 7, p.HEIGHT - 8)

    def __init__(self, app, path: str, meta: dict,
                 on_deleted: Callable[[], None] | None = None) -> None:
        super().__init__(app)
        self.path = path
        self.meta = meta
        self.on_deleted = on_deleted
        self.choice = 2                      # Print
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
        gap = 8
        bw = (w - 24 - 2 * gap) // 3
        by = y2 - 52
        self.buttons = [
            ButtonWidget(f"dlg.b{i}", (x1 + 12 + i * (bw + gap), by, x1 + 12 + i * (bw + gap) + bw - 1, by + 39),
                         label, accent)
            for i, (label, accent) in enumerate((("Delete", t.ERROR), ("Cancel", t.DISABLED),
                                                 ("Print", t.ACCENT)))
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
        self.choice = max(0, min(len(self.buttons) - 1, self.choice + delta))

    def on_click(self) -> None:
        if self.choice == 0:
            self.app.push(Confirm(self.app, "Delete file?", self.path, self._delete,
                                  ok="Delete", danger=True))
            return
        self.app.pop()
        if self.choice == 2:
            self.api.fire("printer.print.start", filename=self.path)
            self.app.pop_to_root()

    def _delete(self) -> None:
        async def run() -> None:
            try:
                await self.api.call("server.files.delete_file", path=f"gcodes/{self.path}")
            except Exception as e:  # noqa: BLE001 - e.g. the file is being printed
                self.app.toast(str(e), "error")
                return
            self.app.toast(f"Deleted {posixpath.basename(self.path)}")
            if self.app.top is self:
                self.app.pop()
            if self.on_deleted:
                self.on_deleted()
        asyncio.ensure_future(run())
