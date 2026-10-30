"""In-process link that feeds a Canvas and answers the handshake."""

from __future__ import annotations

from .. import protocol as p
from ..link import Link
from .renderer import Canvas


class SimLink(Link):
    def __init__(self, canvas: Canvas | None = None) -> None:
        super().__init__()
        self.canvas = canvas or Canvas()
        self._parser = p.FrameParser()
        self.beeps: list[int] = []

    async def open(self) -> None:
        self.closed.clear()

    async def write(self, data: bytes) -> None:
        self.bytes_written += len(data)
        for payload in self._parser.feed(data):
            if payload[:1] == b"\x00":
                self.frames.put_nowait(b"\x00OK")
            self.canvas.apply(payload)

    async def beep(self, ms: int) -> None:
        self.beeps.append(ms)

    def rotate(self, delta: int) -> None:
        self.inputs.put_nowait(p.Rotate(delta))

    def press(self) -> None:
        self.inputs.put_nowait(p.Button(True))

    def release(self) -> None:
        self.inputs.put_nowait(p.Button(False))
