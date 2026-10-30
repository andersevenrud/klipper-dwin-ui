"""Drawing front-end over a Link, with a per-widget render cache.

Widgets hand over the frames they would draw under a stable key. Frames
identical to what is already on the panel are skipped, so a render pass
only costs bytes for what actually changed (~11 KB/s link budget).
"""

from __future__ import annotations

import asyncio
import logging

from . import protocol as p
from .link import Link, LinkClosed

log = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]  # x1, y1, x2, y2 inclusive


def intersects(a: Rect, b: Rect) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


class Display:
    def __init__(self, link: Link, background: int = 0x0000) -> None:
        self.link = link
        self.background = background
        self._cache: dict[str, tuple[Rect, list[bytes]]] = {}
        self._queue: list[bytes] = []
        self.brightness_level = 255

    async def handshake(self, attempts: int = 0, interval: float = 0.5) -> None:
        """Ping until the panel answers OK. attempts=0 retries forever
        (the panel takes a moment to boot after power-up)."""
        n = 0
        while True:
            n += 1
            while not self.link.frames.empty():
                self.link.frames.get_nowait()
            await self.link.write(p.handshake())
            try:
                while True:
                    payload = await asyncio.wait_for(self.link.frames.get(), interval)
                    if p.is_handshake_reply(payload):
                        log.info("display handshake ok")
                        return
            except asyncio.TimeoutError:
                pass
            if self.link.closed.is_set():
                raise LinkClosed("link closed during handshake")
            if attempts and n >= attempts:
                raise TimeoutError("display did not answer handshake")
            if n % 10 == 0:
                log.warning("still waiting for display handshake (%d tries)", n)

    async def init(self, brightness: int) -> None:
        await self.link.write(p.set_dir(1))
        await self.set_brightness(brightness)
        self.clear()
        await self.flush()

    async def set_brightness(self, level: int) -> None:
        self.brightness_level = level
        await self.link.write(p.brightness(level))

    def clear(self, color: int | None = None) -> None:
        if color is not None:
            self.background = color
        self._queue = [p.clear(self.background)]
        self._cache.clear()

    def draw(self, key: str, area: Rect, frames: list[bytes]) -> bool:
        """Queue frames for key unless they match what is drawn. Returns True if queued."""
        cached = self._cache.get(key)
        if cached is not None and cached[1] == frames:
            return False
        self._cache[key] = (area, frames)
        self._queue.extend(frames)
        return True

    def forget(self, key: str) -> None:
        self._cache.pop(key, None)

    def invalidate(self, area: Rect) -> None:
        """Blank an area and force everything overlapping it to redraw."""
        self._queue.append(p.fill(self.background, *area))
        for key in [k for k, (r, _) in self._cache.items() if intersects(r, area)]:
            del self._cache[key]

    def cached_keys(self) -> set[str]:
        return set(self._cache)

    @property
    def pending(self) -> bool:
        return bool(self._queue)

    async def flush(self) -> int:
        if not self._queue:
            return 0
        data = b"".join(self._queue) + p.update()
        self._queue = []
        await self.link.write(data)
        return len(data)
