"""Display links: the one interface between the UI and the display hardware.

A link moves raw DWIN frames to the panel and delivers two streams back:
display reply frames and input events. Backends:

- SerialLink(bridge=True): RP2040 bridge over USB CDC. Input arrives in-band
  as AA EE frames and beeps are sent the same way.
- SerialLink(bridge=False): plain USB-UART adapter; display only, no input.
- PiLink: display on a Raspberry Pi's own UART; encoder and button through
  the kernel's rotary-encoder/gpio-key overlays; optional PWM beeper.
- sim.SimLink: in-process simulator.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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


# -- Raspberry Pi GPIO -----------------------------------------------------------

EVENT_FMT = "llHHi"                 # struct input_event (native time fields)
EVENT_SIZE = struct.calcsize(EVENT_FMT)
EV_KEY, EV_REL = 0x01, 0x02


def find_input(spec: str, prefix: str, sysfs: Path = Path("/sys/class/input")) -> str | None:
    """Input device for spec: a path/glob, or "auto" to match the device
    name the overlay creates ("rotary@11", "button@16")."""
    if spec and spec != "auto":
        matches = sorted(glob.glob(spec))
        return matches[0] if matches else None
    for dev in sorted(sysfs.glob("event*")):
        try:
            name = (dev / "device" / "name").read_text().strip()
        except OSError:
            continue
        if name.startswith(prefix):
            return f"/dev/input/{dev.name}"
    return None


class PwmBeeper:
    """Square wave on a sysfs PWM channel (dtoverlay=pwm)."""

    def __init__(self, chip: str, channel: int = 0, hz: int = 2700) -> None:
        self.chip = Path(chip)
        self.pwm = self.chip / f"pwm{channel}"
        self.channel = channel
        self.hz = hz

    def _write(self, name: str, value: int) -> None:
        (self.pwm / name).write_text(str(value))

    async def setup(self) -> None:
        if not self.pwm.exists():
            (self.chip / "export").write_text(str(self.channel))
            for _ in range(20):           # udev fixes permissions shortly after export
                await asyncio.sleep(0.05)
                if os.access(self.pwm / "enable", os.W_OK):
                    break
        period = round(1e9 / self.hz)
        self._write("enable", 0)
        self._write("duty_cycle", 0)
        self._write("period", period)
        self._write("duty_cycle", period // 2)

    def on(self) -> None:
        self._write("enable", 1)

    def off(self) -> None:
        self._write("enable", 0)


class PiLink(SerialLink):
    """Display wired straight to a Raspberry Pi.

    The UART carries the display frames. The encoder and button come from the
    kernel's rotary-encoder and gpio-key overlays as input devices, so quadrature
    decoding and debouncing happen in the kernel. The beeper is optional, on a
    hardware PWM channel. See install/setup-pi-gpio.sh.
    """

    def __init__(self, port: str, baud: int = 115200, encoder: str = "auto", button: str = "auto",
                 beeper_pwm: str = "", beeper_hz: int = 2700) -> None:
        super().__init__(port, baud, bridge=False)
        self.encoder = encoder
        self.button = button
        self.beeper = PwmBeeper(beeper_pwm, hz=beeper_hz) if beeper_pwm else None
        self._fds: list[int] = []

    async def open(self) -> None:
        await super().open()
        loop = asyncio.get_running_loop()
        for spec, prefix in ((self.encoder, "rotary"), (self.button, "button")):
            path = find_input(spec, prefix)
            if not path:
                log.warning("no %s input device found (%s); check the overlays in config.txt",
                            prefix, spec)
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as e:
                log.warning("cannot open %s: %s (is the user in the 'input' group?)", path, e)
                continue
            log.info("%s input: %s", prefix, path)
            self._fds.append(fd)
            loop.add_reader(fd, self._read_input, fd)
        if self.beeper:
            try:
                await self.beeper.setup()
            except OSError as e:
                log.warning("beeper disabled: %s", e)
                self.beeper = None

    def _read_input(self, fd: int) -> None:
        try:
            data = os.read(fd, EVENT_SIZE * 64)
        except BlockingIOError:
            return
        except OSError as e:
            log.warning("input device read failed: %s", e)
            asyncio.get_running_loop().remove_reader(fd)
            return
        delta = 0
        for offset in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
            _, _, etype, _, value = struct.unpack_from(EVENT_FMT, data, offset)
            if etype == EV_REL:
                delta += value
            elif etype == EV_KEY and value in (0, 1):        # 2 = autorepeat
                if delta:
                    self.inputs.put_nowait(p.Rotate(delta))
                    delta = 0
                self.inputs.put_nowait(p.Button(bool(value)))
        if delta:
            self.inputs.put_nowait(p.Rotate(delta))

    async def beep(self, ms: int) -> None:
        if not self.beeper:
            return
        try:
            self.beeper.on()
        except OSError:
            return
        asyncio.get_running_loop().call_later(ms / 1000, self._beep_off)

    def _beep_off(self) -> None:
        try:
            if self.beeper:
                self.beeper.off()
        except OSError:
            pass

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        for fd in self._fds:
            loop.remove_reader(fd)
            os.close(fd)
        self._fds = []
        self._beep_off()
        await super().close()
