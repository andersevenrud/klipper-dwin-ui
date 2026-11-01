"""Moonraker websocket JSON-RPC client."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from . import PROJECT_URL, __version__
from .printer import DEVICE_TYPES, SUBSCRIBE_OBJECTS, Printer

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0


class RPCError(Exception):
    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.code = code


class Moonraker:
    """Keeps a Printer object in sync and exposes request/notify plumbing.

    The UI uses call() for answers it needs and fire() for long-running
    gcode (G28 holds the RPC open until done), with errors reported through
    on_error.
    """

    def __init__(self, printer: Printer, url: str = "ws://localhost:7125/websocket",
                 api_key: str | None = None) -> None:
        self.printer = printer
        self.url = url
        self.api_key = api_key
        self.on_error: Callable[[str], None] = lambda msg: None
        self.on_gcode_response: Callable[[str], None] = lambda line: None
        self._ws: Any = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._on_result: dict[int, Callable[[Any], None]] = {}
        self._ready_event = asyncio.Event()

    # -- public API -------------------------------------------------------

    async def call(self, method: str, timeout: float | None = DEFAULT_TIMEOUT,
                   on_result: Callable[[Any], None] | None = None, **params: Any) -> Any:
        """on_result runs in the reader, in message order (before any later
        notification), unlike code after the await."""
        if self._ws is None:
            raise RPCError("not connected to Moonraker")
        req_id = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        if on_result:
            self._on_result[req_id] = on_result
        msg = {"jsonrpc": "2.0", "method": method, "id": req_id}
        if params:
            msg["params"] = params
        try:
            await self._ws.send(json.dumps(msg))
            return await asyncio.wait_for(fut, timeout) if timeout else await fut
        finally:
            self._pending.pop(req_id, None)
            self._on_result.pop(req_id, None)

    def fire(self, method: str, **params: Any) -> asyncio.Task:
        """Send without waiting; failures go to on_error."""
        async def run() -> None:
            try:
                await self.call(method, timeout=None, **params)
            except RPCError as e:
                self.on_error(str(e))
            except Exception as e:  # noqa: BLE001 - surface anything to the user
                log.exception("%s failed", method)
                self.on_error(str(e) or type(e).__name__)
        return asyncio.create_task(run())

    async def fetch_file(self, root: str, path: str) -> bytes:
        """Download a file Moonraker serves (e.g. a G-code thumbnail) over HTTP."""
        parts = urllib.parse.urlsplit(self.url)
        scheme = "https" if parts.scheme == "wss" else "http"
        url = f"{scheme}://{parts.netloc}/server/files/{root}/{urllib.parse.quote(path)}"
        headers = {"X-Api-Key": self.api_key} if self.api_key else {}

        def get() -> bytes:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as r:
                return r.read()
        return await asyncio.get_running_loop().run_in_executor(None, get)

    async def gcode(self, script: str) -> None:
        await self.call("printer.gcode.script", timeout=None, script=script)

    def gcode_fire(self, script: str) -> asyncio.Task:
        return self.fire("printer.gcode.script", script=script)

    # -- connection -------------------------------------------------------

    async def run(self) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import WebSocketException

        while True:
            try:
                async with connect(self.url, max_size=16 * 1024 * 1024,
                                   open_timeout=10) as ws:
                    self._ws = ws
                    log.info("connected to %s", self.url)
                    reader = asyncio.create_task(self._read_loop(ws))
                    setup = asyncio.create_task(self._session())
                    done, _ = await asyncio.wait({reader, setup},
                                                 return_when=asyncio.FIRST_COMPLETED)
                    setup.cancel()
                    reader.cancel()
                    for t in done:
                        if not t.cancelled() and t.exception():
                            log.warning("session ended: %s", t.exception())
            except (OSError, WebSocketException, asyncio.TimeoutError) as e:
                log.warning("moonraker connection failed: %s", e)
            finally:
                self._ws = None
                self._fail_pending("connection lost")
                self.printer.connected = False
                self.printer.set_state("disconnected", "Moonraker not reachable")
                self.printer.changed()
            await asyncio.sleep(3)

    def _fail_pending(self, why: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RPCError(why))
        self._pending.clear()

    async def _session(self) -> None:
        params: dict[str, Any] = {
            "client_name": "dwinui", "version": __version__,
            "type": "display", "url": PROJECT_URL,
        }
        if self.api_key:
            params["api_key"] = self.api_key
        await self.call("server.connection.identify", **params)
        self.printer.connected = True
        await self._load_power_devices()
        self.printer.changed()
        await self._klippy_monitor()

    async def _load_power_devices(self) -> None:
        """Moonraker [power] devices; none if the power component isn't loaded."""
        try:
            res = await self.call("machine.device_power.devices")
        except RPCError:
            self.printer.power = {}
            return
        self.printer.power = {d["device"]: d for d in res.get("devices", []) if "device" in d}

    async def _klippy_monitor(self) -> None:
        """Poll Klippy until ready, subscribe, then wait for it to drop."""
        while True:
            if self.printer.klippy_state != "ready" or not self.printer.status:
                try:
                    info = await self.call("printer.info")
                    state, message = info.get("state", "error"), info.get("state_message", "")
                except RPCError as e:
                    state, message = "disconnected", str(e)
                if state == "ready":
                    await self._subscribe()
                else:
                    self.printer.status = {}
                    self.printer.set_state(state, message)
            self._ready_event.clear()
            try:
                await asyncio.wait_for(self._ready_event.wait(), 2.0)
            except asyncio.TimeoutError:
                pass

    async def _subscribe(self) -> None:
        def apply(res: Any) -> None:
            self.printer.replace(res.get("status", {}))
            self.printer.set_state("ready", "")

        # Sensors, fans, lights and runout sensors differ per printer: ask
        # Klipper which exist, then subscribe to all of them.
        res = await self.call("printer.objects.query", objects={"heaters": None})
        heaters = res.get("status", {}).get("heaters", {})
        sensors = [*heaters.get("available_heaters", []), *heaters.get("available_sensors", [])]
        listing = await self.call("printer.objects.list")
        self.printer.objects = list(listing.get("objects", []))
        devices = [o for o in self.printer.objects if o.split()[0] in DEVICE_TYPES]
        names = dict.fromkeys([*SUBSCRIBE_OBJECTS, *sensors, *devices])
        objects: dict[str, list[str] | None] = {name: None for name in names}
        # Only whether SAVE_CONFIG is pending; the full config would be large.
        objects["configfile"] = ["save_config_pending", "save_config_pending_items"]
        # Live toolhead and extruder speed for the printing screen; not the
        # positions and stepper lists, which change constantly.
        objects["motion_report"] = ["live_velocity", "live_extruder_velocity"]
        # Calibration helpers, when configured: the screens that take over
        # while Klipper waits for input, and the screws tilt result. And the
        # print's objects, for cancelling one.
        for name in ("manual_probe", "bed_screws", "screws_tilt_adjust", "exclude_object"):
            if name in self.printer.objects:
                objects[name] = None
        await self.call("printer.objects.subscribe", on_result=apply, objects=objects)
        log.info("subscribed to printer objects")

    async def _read_loop(self, ws: Any) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if "id" in msg and msg["id"] in self._pending:
                fut = self._pending[msg["id"]]
                if fut.done():
                    continue
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(RPCError(err.get("message", "error"), err.get("code", 0)))
                else:
                    hook = self._on_result.get(msg["id"])
                    if hook:
                        hook(msg.get("result"))
                    fut.set_result(msg.get("result"))
            elif "method" in msg:
                self._notify(msg["method"], msg.get("params") or [])

    def _notify(self, method: str, params: list) -> None:
        if method == "notify_status_update" and params:
            self.printer.update(params[0])
        elif method == "notify_klippy_ready":
            self.printer.status = {}
            self.printer.set_state("startup", "")
            self._ready_event.set()
        elif method == "notify_klippy_shutdown":
            self.printer.set_state("shutdown", self.printer.state_message)
            self._ready_event.set()
        elif method == "notify_klippy_disconnected":
            self.printer.status = {}
            self.printer.set_state("disconnected", "Klipper disconnected")
            self._ready_event.set()
        elif method == "notify_gcode_response" and params:
            self.on_gcode_response(str(params[0]))
        elif method == "notify_power_changed" and params:
            device = params[0]
            if isinstance(device, dict) and "device" in device:
                self.printer.power[device["device"]] = device
                self.printer.changed()
