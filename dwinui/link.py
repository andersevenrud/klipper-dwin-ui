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
import threading
from concurrent.futures import ThreadPoolExecutor

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
    """pyserial with a reader thread and a single writer thread.

    (serial_asyncio_fast spins forever in its write callback on macOS once a
    write exceeds the OS buffer, so plain blocking I/O in threads it is.)
    """

    def __init__(self, port: str, baud: int = 115200, bridge: bool = True) -> None:
        super().__init__()
        self.port = port
        self.baud = baud
        self.bridge = bridge
        self._serial = None
        self._writer: ThreadPoolExecutor | None = None
        self._stop = threading.Event()

    async def open(self) -> None:
        import serial

        port = resolve_port(self.port)
        log.info("opening %s", port)
        try:
            self._serial = serial.Serial(port, self.baud, timeout=0.1, write_timeout=5)
        except (OSError, serial.SerialException) as e:
            raise LinkClosed(str(e)) from e
        self.closed.clear()
        self._stop.clear()
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dwin-write")
        loop = asyncio.get_running_loop()
        threading.Thread(target=self._read_thread, args=(loop, self._serial), name="dwin-read",
                         daemon=True).start()

    def _read_thread(self, loop: asyncio.AbstractEventLoop, ser) -> None:
        parser = p.FrameParser()
        try:
            while not self._stop.is_set():
                data = ser.read(ser.in_waiting or 1)
                for payload in parser.feed(data):
                    loop.call_soon_threadsafe(self._dispatch, payload)
        except Exception as e:  # noqa: BLE001 - unplugged, closed under us, ...
            if not self._stop.is_set():
                log.warning("serial read failed: %s", e)
        finally:
            loop.call_soon_threadsafe(self.closed.set)

    async def write(self, data: bytes) -> None:
        if self.closed.is_set() or not self._serial or not self._writer:
            raise LinkClosed("link closed")
        try:
            await asyncio.get_running_loop().run_in_executor(self._writer, self._serial.write, data)
        except Exception as e:  # noqa: BLE001 - SerialException, SerialTimeoutException, OSError
            self.closed.set()
            raise LinkClosed(str(e)) from e
        self.bytes_written += len(data)

    async def beep(self, ms: int) -> None:
        if self.bridge:
            await self.write(p.bridge_beep(ms))

    async def close(self) -> None:
        self._stop.set()
        if self._writer:
            self._writer.shutdown(wait=False, cancel_futures=True)
            self._writer = None
        if self._serial:
            try:
                self._serial.close()
            except Exception:  # noqa: BLE001
                pass
        await super().close()
