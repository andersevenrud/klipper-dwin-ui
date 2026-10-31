"""A stand-in for Moonraker + Klipper, good enough to drive every screen."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from typing import Any

from ..moonraker import RPCError
from ..printer import Printer

log = logging.getLogger(__name__)

FILES = [
    ("benchy.gcode", 3 * 3600, 2 * 3600 + 14 * 60, 5230.0),
    ("3DBenchy_PLA_31m20s.gcode", 4 * 3600, 31 * 60 + 20, 3900.0),
    ("calibration/cube_20mm.gcode", 5 * 86400, 38 * 60, 1840.0),
    ("calibration/temp_tower/tower_190-230.gcode", 9 * 86400, 52 * 60, 4100.0),
    ("voron_cable_chain_x.gcode", 26 * 3600, 5 * 3600 + 2 * 60, 21400.0),
    ("filament_clip.gcode", 900, 9 * 60, 310.0),
]
# Mirrors a real printer's sensor set (Cartographer probe, CAN toolhead).
SENSORS = {
    "temperature_sensor Chamber": 21.0, "temperature_sensor Toolhead_MCU": 31.0,
    "temperature_fan Mainboard_MCU": 29.0, "temperature_sensor cartographer_coil": 24.0,
    "temperature_sensor Cartographer_MCU": 26.0, "temperature_fan Host_MCU": 36.0,
}
# Fans and lights, to exercise every kind the UI handles.
DEVICES = {
    "fan_generic exhaust": {"speed": 0.0},
    "heater_fan hotend": {"speed": 0.0},
    "controller_fan electronics": {"speed": 0.4},
    "output_pin caselight": {"value": 0.0},
    "output_pin relay": {"value": 0.0},
    "output_pin _beeper": {"value": 0.0},
    "neopixel toolhead": {"color_data": [[0.0, 0.0, 0.0, 0.0]]},
}
CONFIG_SETTINGS = {
    "output_pin caselight": {"pwm": True, "scale": 1.0},
    "output_pin relay": {"pwm": False, "scale": 1.0},
    "output_pin _beeper": {"pwm": True, "scale": 1.0},
}
# Moonraker [power] devices. "printer" behaves like the printer's supply:
# off makes the MCU unreachable, on brings Klipper back.
POWER = {
    "printer": {"device": "printer", "status": "on", "locked_while_printing": True, "type": "gpio"},
    "lights": {"device": "lights", "status": "off", "locked_while_printing": False,
               "type": "tplink_smartplug"},
}
MACROS = ["PRINT_START", "PRINT_END", "LOAD_FILAMENT", "UNLOAD_FILAMENT", "BED_MESH_CALIBRATE",
          "_HOMING_VARS", "PARK"]


class FakeMoonraker:
    def __init__(self, printer: Printer, speedup: float = 1.0, db: dict | None = None) -> None:
        self.printer = printer
        self.speedup = speedup
        self.db: dict[str, dict] = db if db is not None else {}     # Moonraker's database
        start = time.time() - 600
        self.gcode_store: list[dict] = [
            {"message": "// Klipper state: Ready", "time": start, "type": "response"},
            {"message": "G28", "time": start + 60, "type": "command"},
            {"message": "// probe at 110.000,110.000 is z=1.234567", "time": start + 75, "type": "response"},
        ]
        self.on_error = lambda msg: None
        self.on_gcode_response = lambda line: None
        self.gcode_log: list[str] = []
        self.calls: list[tuple[str, dict]] = []
        self._print_total = 0.0
        now = time.time()
        self.files = {path: now - age for path, age, _, _ in FILES}      # path -> mtime
        # Print history, newest first, with repeats and failures like a real one.
        self.history = [
            ("benchy.gcode", "completed", now - 2 * 3600),
            ("benchy.gcode", "klippy_shutdown", now - 3 * 3600),
            ("calibration/cube_20mm.gcode", "cancelled", now - 30 * 3600),
            ("old_part_since_deleted.gcode", "completed", now - 5 * 86400),
            ("filament_clip.gcode", "completed", now - 6 * 86400),
        ]

    def _initial(self) -> dict[str, dict[str, Any]]:
        return {
            "webhooks": {"state": "ready", "state_message": "Printer is ready"},
            "print_stats": {"state": "standby", "filename": "", "print_duration": 0.0,
                            "total_duration": 0.0, "filament_used": 0.0, "message": ""},
            "display_status": {"progress": 0.0, "message": None},
            "virtual_sdcard": {"progress": 0.0, "is_active": False},
            "extruder": {"temperature": 23.4, "target": 0.0, "power": 0.0},
            "heater_bed": {"temperature": 22.1, "target": 0.0, "power": 0.0},
            "toolhead": {"homed_axes": "", "position": [0, 0, 0, 0]},
            "gcode_move": {"speed_factor": 1.0, "extrude_factor": 1.0,
                           "gcode_position": [0.0, 0.0, 0.0, 0.0],
                           "homing_origin": [0.0, 0.0, 0.0, 0.0]},
            "fan": {"speed": 0.0},
            "idle_timeout": {"state": "Idle"},
            "heaters": {"available_heaters": ["heater_bed", "extruder"],
                        "available_sensors": ["heater_bed", *SENSORS, "extruder"]},
            **{name: ({"temperature": temp, "target": 45.0, "speed": 0.3}
                      if name.startswith("temperature_fan") else {"temperature": temp})
               for name, temp in SENSORS.items()},
            **{name: dict(status) for name, status in DEVICES.items()},
            "configfile": {"settings": CONFIG_SETTINGS},
        }

    async def run(self) -> None:
        self.printer.connected = True
        self.printer.replace(self._initial())
        self.printer.objects = list(self.printer.status) + [f"gcode_macro {m}" for m in MACROS]
        self.printer.power = {name: dict(device) for name, device in POWER.items()}
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            self._tick((now - last) * self.speedup)
            last = now

    def _tick(self, dt: float) -> None:
        if not self.printer.ready:
            return
        upd: dict[str, dict] = {}
        for heater, rate in (("extruder", 3.0), ("heater_bed", 1.0)):
            cur, target = self.printer.temp(heater)
            goal = target if target > 0 else 22.0
            step = rate * dt
            new = min(goal, cur + step) if cur < goal else max(goal, cur - step * 0.5)
            upd[heater] = {"temperature": round(new, 1)}
        hot = self.printer.temp("extruder")[0] > 50
        upd["heater_fan hotend"] = {"speed": 1.0 if hot else 0.0}
        ps = self.printer.status.get("print_stats", {})
        if ps.get("state") == "printing" and self._print_total:
            dur = ps.get("print_duration", 0.0) + dt
            progress = min(1.0, dur / self._print_total)
            upd["print_stats"] = {"print_duration": dur, "total_duration": dur,
                                  "filament_used": ps.get("filament_used", 0.0) + dt * 0.6}
            upd["display_status"] = {"progress": progress}
            upd["gcode_move"] = {"gcode_position": [110.0, 110.0, round(0.2 + progress * 40, 2), 0.0]}
            if progress >= 1.0:
                upd["print_stats"]["state"] = "complete"
        self.printer.update(upd)

    # -- RPC ------------------------------------------------------------

    async def call(self, method: str, timeout: float | None = None, **params: Any) -> Any:
        self.calls.append((method, params))
        await asyncio.sleep(0.01)
        handler = getattr(self, "_m_" + method.replace(".", "_"), None)
        if handler is None:
            raise RPCError(f"Method not found: {method}", -32601)
        return await handler(**params)

    def fire(self, method: str, **params: Any) -> asyncio.Task:
        async def run() -> None:
            try:
                await self.call(method, **params)
            except RPCError as e:
                self.on_error(str(e))
        return asyncio.ensure_future(run())

    async def fetch_file(self, root: str, path: str) -> bytes:
        """A made-up slicer thumbnail: a sliced part on a transparent background."""
        import io

        from PIL import Image, ImageDraw
        size = int(path.rsplit("-", 1)[-1].split("x")[0])
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        s = size / 300
        d.polygon([(60 * s, 200 * s), (150 * s, 250 * s), (240 * s, 200 * s), (150 * s, 150 * s)], fill=(46, 120, 60))
        d.polygon([(60 * s, 200 * s), (60 * s, 90 * s), (150 * s, 40 * s), (150 * s, 150 * s)], fill=(70, 170, 90))
        d.polygon([(150 * s, 150 * s), (150 * s, 40 * s), (240 * s, 90 * s), (240 * s, 200 * s)], fill=(55, 145, 75))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        await asyncio.sleep(0.01)
        return buf.getvalue()

    async def gcode(self, script: str) -> None:
        await self.call("printer.gcode.script", script=script)

    def gcode_fire(self, script: str) -> asyncio.Task:
        return self.fire("printer.gcode.script", script=script)

    async def _m_server_files_list(self, root: str = "gcodes") -> list[dict]:
        return [{"path": p, "modified": m, "size": 1_000_000, "permissions": "rw"}
                for p, m in self.files.items()]

    async def _m_server_files_get_directory(self, path: str = "gcodes", extended: bool = False) -> dict:
        prefix = path.partition("/")[2]
        prefix = prefix + "/" if prefix else ""
        dirs, files = {}, []
        for p, m in self.files.items():
            if not p.startswith(prefix):
                continue
            rest = p[len(prefix):]
            if "/" in rest:
                dirs[rest.split("/")[0]] = max(m, dirs.get(rest.split("/")[0], 0))
            else:
                files.append({"filename": rest, "modified": m, "size": 1_000_000, "permissions": "rw"})
        if not prefix:
            dirs[".thumbs"] = time.time()
        return {"dirs": [{"dirname": d, "modified": m, "size": 4096, "permissions": "rw"}
                         for d, m in dirs.items()],
                "files": files, "root_info": {"name": "gcodes", "permissions": "rw"}}

    async def _m_server_files_delete_file(self, path: str) -> dict:
        rel = path.partition("/")[2]
        if rel not in self.files:
            raise RPCError(f"File {path} does not exist", 404)
        if self.printer.job_active and self.printer.get("print_stats", "filename") == rel:
            raise RPCError(f"File {path} is currently being printed", 403)
        del self.files[rel]
        return {"item": {"path": rel, "root": "gcodes"}, "action": "delete_file"}

    async def _m_server_history_list(self, limit: int = 50, order: str = "desc", **_) -> dict:
        jobs = [{"job_id": f"{i:06X}", "filename": name, "status": status, "start_time": end - 1800,
                 "end_time": end, "print_duration": 1700.0, "exists": name in self.files}
                for i, (name, status, end) in enumerate(self.history)][:limit]
        return {"count": len(jobs), "jobs": jobs}

    async def _m_server_files_metadata(self, filename: str) -> dict:
        for path, _, est, fil in FILES:
            if path == filename and path in self.files:
                base = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                thumbs = [] if "cable_chain" in path else [
                    {"width": w, "height": w, "size": 0, "relative_path": f".thumbs/{base}-{w}x{w}.png"}
                    for w in (32, 300)]
                return {"filename": path, "estimated_time": est, "filament_total": fil,
                        "layer_height": 0.2, "object_height": 48.0, "slicer": "OrcaSlicer",
                        "slicer_version": "2.2.0", "thumbnails": thumbs}
        raise RPCError(f"Metadata not available for <{filename}>", 404)

    async def _m_printer_objects_query(self, objects: dict) -> dict:
        return {"eventtime": time.monotonic(),
                "status": {name: dict(self.printer.status.get(name, {})) for name in objects}}

    async def _m_server_temperature_store(self, include_monitors: bool = False) -> dict:
        """Ten minutes of 1 s samples: a preheat to 210/60 that was turned off again."""
        n = 600

        def curve(peak: float, rate: float) -> list[float]:
            out = []
            for i in range(n):
                if i < 200:
                    out.append(22.0)
                elif i < 450:
                    out.append(22 + (peak - 22) * (1 - math.exp(-(i - 200) / rate)))
                else:
                    out.append(22 + (out[449] - 22) * math.exp(-(i - 450) / (rate * 3)))
            return [round(v, 2) for v in out]

        def target(value: float) -> list[float]:
            return [value if 200 <= i < 450 else 0.0 for i in range(n)]

        store = {"extruder": {"temperatures": curve(210, 25), "targets": target(210),
                              "powers": [0.0] * n},
                 "heater_bed": {"temperatures": curve(60, 70), "targets": target(60),
                                "powers": [0.0] * n}}
        # The live values carry on from where the history ends.
        self.printer.update({"extruder": {"temperature": store["extruder"]["temperatures"][-1]},
                             "heater_bed": {"temperature": store["heater_bed"]["temperatures"][-1]}})
        return store

    async def _m_printer_objects_list(self) -> dict:
        return {"objects": list(self.printer.status) + [f"gcode_macro {m}" for m in MACROS]}

    async def _m_printer_print_start(self, filename: str) -> str:
        if self.printer.job_active:
            raise RPCError("Printer is busy", 400)
        est = next((e for p, _, e, _ in FILES if p == filename), 1800)
        self._print_total = est / 30   # demo prints run 30x faster
        self.printer.update({
            "print_stats": {"state": "printing", "filename": filename, "print_duration": 0.0,
                            "filament_used": 0.0},
            "display_status": {"progress": 0.0},
            "extruder": {"target": 210.0}, "heater_bed": {"target": 60.0},
            "toolhead": {"homed_axes": "xyz"},
        })
        return "ok"

    async def _m_printer_print_pause(self) -> str:
        self.printer.update({"print_stats": {"state": "paused"}})
        return "ok"

    async def _m_printer_print_resume(self) -> str:
        self.printer.update({"print_stats": {"state": "printing"}})
        return "ok"

    async def _m_printer_print_cancel(self) -> str:
        self.printer.update({"print_stats": {"state": "cancelled"},
                             "extruder": {"target": 0.0}, "heater_bed": {"target": 0.0}})
        return "ok"

    async def _m_printer_emergency_stop(self) -> str:
        msg = "Shutdown due to webhooks request"
        self.printer.update({"webhooks": {"state": "shutdown", "state_message": msg}})
        return "ok"

    async def _m_printer_firmware_restart(self) -> str:
        return await self._restart()

    async def _m_printer_restart(self) -> str:
        return await self._restart()

    async def _restart(self) -> str:
        self.printer.status = {}
        self.printer.set_state("startup", "Klipper is starting")

        async def back() -> None:
            await asyncio.sleep(1.5)
            self.printer.replace(self._initial())
        asyncio.ensure_future(back())
        return "ok"

    async def _m_machine_device_power_devices(self) -> dict:
        return {"devices": [dict(d) for d in self.printer.power.values()]}

    async def _m_machine_device_power_post_device(self, device: str, action: str) -> dict:
        if device not in self.printer.power:
            raise RPCError(f"No valid power device named {device}", 404)
        if self.printer.power_locked(device):
            raise RPCError(f"Unable to change power for {device} while printing", 400)
        state = {"on": "on", "off": "off"}.get(action) or (
            "off" if self.printer.power[device]["status"] == "on" else "on")
        self.printer.power[device]["status"] = state
        self.printer.changed()
        if device == "printer":
            if state == "off":
                self.printer.status = {}
                self.printer.set_state("error", "mcu 'mcu': Unable to connect")
            else:
                await self._restart()
        return {device: state}

    async def _m_server_database_get_item(self, namespace: str, key: str | None = None) -> dict:
        if namespace not in self.db:
            raise RPCError(f"Namespace {namespace} not found", 404)
        if key is None:
            return {"namespace": namespace, "key": None, "value": dict(self.db[namespace])}
        if key not in self.db[namespace]:
            raise RPCError(f"Key '{key}' in namespace '{namespace}' not found", 404)
        return {"namespace": namespace, "key": key, "value": self.db[namespace][key]}

    async def _m_server_database_post_item(self, namespace: str, key: str, value) -> dict:
        self.db.setdefault(namespace, {})[key] = value
        return {"namespace": namespace, "key": key, "value": value}

    async def _m_server_database_delete_item(self, namespace: str, key: str) -> dict:
        value = self.db.get(namespace, {}).pop(key, None)
        return {"namespace": namespace, "key": key, "value": value}

    async def _m_machine_reboot(self) -> str:
        return "ok"

    async def _m_machine_shutdown(self) -> str:
        return "ok"

    async def _m_printer_gcode_script(self, script: str) -> str:
        self._store("command", script)
        try:
            for line in script.splitlines():
                self.gcode_log.append(line)
                self._gcode(line.strip())
        except RPCError as e:
            # Klipper also broadcasts the error as a gcode response.
            self._store("response", f"!! {e}")
            self.on_gcode_response(f"!! {e}")
            raise
        return "ok"

    def _store(self, kind: str, message: str) -> None:
        self.gcode_store.append({"message": message, "time": time.time(), "type": kind})
        del self.gcode_store[:-1000]

    async def _m_server_gcode_store(self, count: int = 100) -> dict:
        return {"gcode_store": self.gcode_store[-count:]}

    def _gcode(self, line: str) -> None:
        pr = self.printer
        words = dict(re.findall(r"([A-Z_]+)=(\S+)", line.upper()))
        cmd = line.split()[0].upper() if line else ""
        letters = {m[0]: float(m[1:]) for m in line.upper().split()[1:]
                   if re.fullmatch(r"[A-Z]-?[\d.]+", m)}
        if cmd == "SET_HEATER_TEMPERATURE":
            pr.update({words["HEATER"].lower(): {"target": float(words.get("TARGET", 0))}})
        elif cmd == "TURN_OFF_HEATERS":
            pr.update({"extruder": {"target": 0.0}, "heater_bed": {"target": 0.0}})
        elif cmd == "M220":
            pr.update({"gcode_move": {"speed_factor": letters.get("S", 100) / 100}})
        elif cmd == "M221":
            pr.update({"gcode_move": {"extrude_factor": letters.get("S", 100) / 100}})
        elif cmd == "M106":
            pr.update({"fan": {"speed": letters.get("S", 255) / 255}})
        elif cmd == "G28":
            axes = "".join(a for a in "XYZ" if a in line.upper()[3:]) or "XYZ"
            homed = "".join(sorted(set(pr.homed_axes) | set(axes.lower())))
            pos = list(pr.get("gcode_move", "gcode_position", [0, 0, 0, 0]))
            for a in axes:
                pos["XYZ".index(a)] = 0.0
            pr.update({"toolhead": {"homed_axes": homed}, "gcode_move": {"gcode_position": pos}})
        elif cmd == "G1":
            pos = list(pr.get("gcode_move", "gcode_position", [0, 0, 0, 0]))
            for i, a in enumerate("XYZ"):
                if a in letters:
                    if a.lower() not in pr.homed_axes:
                        raise RPCError("Must home axis first: 0.000 0.000 0.000 [0.000]", 400)
                    pos[i] = round(pos[i] + letters[a], 3)
            pr.update({"gcode_move": {"gcode_position": pos}})
        elif cmd == "SET_GCODE_OFFSET":
            origin = list(pr.get("gcode_move", "homing_origin", [0, 0, 0, 0]))
            origin[2] = round(origin[2] + float(words.get("Z_ADJUST", 0)), 3)
            pr.update({"gcode_move": {"homing_origin": origin}})
        elif cmd == "M84":
            pr.update({"toolhead": {"homed_axes": ""}})
        elif cmd == "SET_FAN_SPEED":
            pr.update({f"fan_generic {words['FAN'].lower()}": {"speed": float(words["SPEED"])}})
        elif cmd == "SET_PIN":
            pr.update({f"output_pin {words['PIN'].lower()}": {"value": float(words["VALUE"])}})
        elif cmd == "SET_LED":
            name = next(o for o in pr.status if o.split()[0] in ("led", "neopixel")
                        and o.split()[-1].upper() == words["LED"])
            rgb = [float(words.get(c, 0)) for c in ("RED", "GREEN", "BLUE")]
            pr.update({name: {"color_data": [rgb + [0.0]]}})
