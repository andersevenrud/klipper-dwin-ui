"""Settings changed on the display, kept in Moonraker's database.

dwinui.conf provides the defaults. Whatever is changed on the display (the
backlight, jog step, extrude length...) is saved in the "dwinui" namespace of
Moonraker's database, so it survives restarts and wins over the config file
until reset from System.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from .printer import Printer

log = logging.getLogger(__name__)

NAMESPACE = "dwinui"


class Settings:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.loaded = False
        self._listeners: list[Callable[[], None]] = []

    def listen(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, api, key: str, value: Any) -> None:
        """Remember a value and save it (fire-and-forget; a failed save only
        means it won't survive a restart)."""
        self.values[key] = value
        api.fire("server.database.post_item", namespace=NAMESPACE, key=key, value=value)

    async def reset(self, api) -> None:
        for key in list(self.values):
            try:
                await api.call("server.database.delete_item", namespace=NAMESPACE, key=key)
            except Exception as e:  # noqa: BLE001 - already gone is fine
                log.info("could not delete setting %s: %s", key, e)
        self.values.clear()
        self._changed()

    async def load(self, api) -> None:
        try:
            res = await api.call("server.database.get_item", namespace=NAMESPACE)
            value = res.get("value", {}) if isinstance(res, dict) else {}
            self.values = dict(value) if isinstance(value, dict) else {}
        except Exception as e:  # noqa: BLE001 - "Namespace dwinui not found": nothing saved yet
            log.debug("no saved settings: %s", e)
            self.values = {}
        self.loaded = True
        self._changed()

    def _changed(self) -> None:
        for fn in self._listeners:
            fn()

    async def run(self, printer: Printer, api) -> None:
        """Load the saved settings every time Moonraker (re)connects."""
        loaded_for_connection = False
        while True:
            if printer.connected and not loaded_for_connection:
                await self.load(api)
                loaded_for_connection = True
            elif not printer.connected:
                loaded_for_connection = False
            await asyncio.sleep(0.5)
