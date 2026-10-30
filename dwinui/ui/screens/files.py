"""G-code file browser with a metadata confirm step."""

from __future__ import annotations

import asyncio
import time

from ..screen import Confirm, Item, ListScreen
from .common import fmt_duration


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
                 action=lambda path=f["path"]: asyncio.ensure_future(self._confirm(path)))
            for f in files
        ])
        self.empty_text = "No G-code files"
        self.app.invalidate()

    async def _confirm(self, path: str) -> None:
        try:
            meta = await self.api.call("server.files.metadata", filename=path)
        except Exception:  # noqa: BLE001 - metadata is optional (no slicer info)
            meta = {}
        lines = [path]
        if meta.get("estimated_time"):
            lines.append(f"Time: {fmt_duration(meta['estimated_time'])}")
        if meta.get("filament_total"):
            lines.append(f"Filament: {meta['filament_total'] / 1000:.2f} m")
        if meta.get("layer_height"):
            lines.append(f"Layer: {meta['layer_height']} mm")
        if meta.get("slicer"):
            lines.append(f"Slicer: {meta['slicer']} {meta.get('slicer_version', '')}".strip())

        def start() -> None:
            self.api.fire("printer.print.start", filename=path)
            self.app.pop_to_root()

        self.app.push(Confirm(self.app, "Start print?", "\n".join(lines), start,
                              ok="Print", default_ok=True))
