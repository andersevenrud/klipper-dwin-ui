"""Run gcode macros discovered via printer.objects.list."""

from __future__ import annotations

import asyncio

from ..screen import Confirm, Item, ListScreen


class MacrosScreen(ListScreen):
    title = "Macros"

    def on_enter(self) -> None:
        self.empty_text = "Loading..."
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        try:
            res = await self.api.call("printer.objects.list")
        except Exception as e:  # noqa: BLE001
            self.empty_text = "Could not list macros"
            self.app.toast(str(e), "error")
            return
        names = sorted(
            obj.split(None, 1)[1].upper()
            for obj in res.get("objects", [])
            if obj.startswith("gcode_macro ")
        )
        if not self.cfg.show_hidden_macros:
            names = [n for n in names if not n.startswith("_")]
        self.set_items([Item(n, action=lambda n=n: self._run(n)) for n in names])
        self.empty_text = "No macros"
        self.app.invalidate()

    def _run(self, name: str) -> None:
        def go() -> None:
            self.api.gcode_fire(name)
            self.app.toast(f"Running {name}")
        if self.cfg.confirm_macros:
            self.app.push(Confirm(self.app, "Run macro?", name, go, ok="Run", default_ok=True))
        else:
            go()
