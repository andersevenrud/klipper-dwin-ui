"""Heater temperature history for graphs, sampled at a fixed interval."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable

from .printer import Printer

log = logging.getLogger(__name__)

Sample = tuple[float, float]   # temperature, target


class TempHistory:
    def __init__(self, interval: float = 5.0, size: int = 240) -> None:
        self.interval = interval
        self.size = size
        self.data: dict[str, deque[Sample]] = {}
        self.count = 0          # samples appended so far; drives incremental drawing
        self.generation = 0     # bumps whenever the history is replaced wholesale

    def reset(self, names: list[str]) -> None:
        self.data = {n: deque(maxlen=self.size) for n in names}
        self.generation += 1

    def load_store(self, store: dict, names: list[str]) -> None:
        """Seed from Moonraker's server.temperature_store (1 s samples)."""
        step = max(1, round(self.interval))
        self.reset(names)
        for name in names:
            entry = store.get(name) or {}
            temps = entry.get("temperatures") or []
            targets = entry.get("targets") or [0.0] * len(temps)
            # Pick every step-th sample counting back from the newest.
            picked = list(zip(temps, targets))[::-1][::step][::-1]
            self.data[name].extend(picked[-self.size:])

    def append(self, values: dict[str, Sample]) -> None:
        for name, sample in values.items():
            self.data.setdefault(name, deque(maxlen=self.size)).append(sample)
        self.count += 1

    async def run(self, printer: Printer, api, on_change: Callable[[], None]) -> None:
        loaded: list[str] | None = None
        while True:
            if printer.ready:
                names = printer.heater_names
                if names != loaded:
                    try:
                        self.load_store(await api.call("server.temperature_store"), names)
                    except Exception as e:  # noqa: BLE001 - start empty instead
                        log.info("no temperature history: %s", e)
                        self.reset(names)
                    loaded = names
                self.append({n: printer.temp(n) for n in names})
                on_change()
                await asyncio.sleep(self.interval)
            else:
                loaded = None
                await asyncio.sleep(0.5)    # pick up the history as soon as Klippy is ready
