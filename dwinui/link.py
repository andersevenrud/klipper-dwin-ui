"""Display links: the one interface between the UI and the display hardware.

A link moves raw DWIN frames to the panel and delivers two streams back:
display reply frames and input events. Backends:

- SerialLink(bridge=True): RP2040 bridge over USB CDC. Input arrives in-band
  as AA EE frames and beeps are sent the same way.
- SerialLink(bridge=False): plain USB-UART adapter; display only, no input.
- sim.SimLink: in-process simulator.

A Pi GPIO backend would subclass Link, read the UART for frames and evdev
for input.
"""

from __future__ import annotations

import asyncio
import glob
import logging

from . import protocol as p

log = logging.getLogger(__name__)


class LinkClosed(Exception):
    pass


class Link:
    def __init__(self) -> None:
        self.frames: asyncio.Queue[bytes] = asyncio.Queue()
        self.inputs: asyncio.Queue[p.InputEvent] = asyncio.Queue()
        self.closed = asyncio.Event()
        self.bytes_written = 0

    async def open(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        self.closed.set()

    async def write(self, data: bytes) -> None:
        raise NotImplementedError

    async def beep(self, ms: int) -> None:
        pass

    def _dispatch(self, payload: bytes) -> None:
        ev = p.parse_input(payload)
        if ev is not None:
            self.inputs.put_nowait(ev)
        else:
            self.frames.put_nowait(payload)


def resolve_port(pattern: str) -> str:
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise LinkClosed(f"no serial port matches {pattern}")
    return matches[0]


class SerialLink(Link):
    def __init__(self, port: str, baud: int = 115200, bridge: bool = True) -> None:
        super().__init__()
        self.port = port
        self.baud = baud
        self.bridge = bridge
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None

    async def open(self) -> None:
        import serial_asyncio_fast

        port = resolve_port(self.port)
        log.info("opening %s", port)
        try:
            self._reader, self._writer = await serial_asyncio_fast.open_serial_connection(
                url=port, baudrate=self.baud)
        except OSError as e:
            raise LinkClosed(str(e)) from e
        self.closed.clear()
        self._task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        parser = p.FrameParser()
        assert self._reader
        try:
            while True:
                data = await self._reader.read(256)
                if not data:
                    break
                for payload in parser.feed(data):
                    self._dispatch(payload)
        except (OSError, asyncio.IncompleteReadError) as e:
            log.warning("serial read failed: %s", e)
        finally:
            self.closed.set()

    async def write(self, data: bytes) -> None:
        if self.closed.is_set() or not self._writer:
            raise LinkClosed("link closed")
        try:
            self._writer.write(data)
            await self._writer.drain()
        except OSError as e:
            self.closed.set()
            raise LinkClosed(str(e)) from e
        self.bytes_written += len(data)

    async def beep(self, ms: int) -> None:
        if self.bridge:
            await self.write(p.bridge_beep(ms))

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        if self._writer:
            self._writer.close()
        await super().close()
