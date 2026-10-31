"""A stand-in for Moonraker + Klipper, good enough to drive every screen."""

from __future__ import annotations

import asyncio
import bisect
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
SCREWS = {"screw1": [30, 30], "screw1_name": "front left", "screw2": [200, 30],
          "screw2_name": "front right", "screw3": [200, 200], "screw3_name": "rear right",
          "screw4": [30, 200], "screw4_name": "rear left"}
CONFIG_SETTINGS = {
    "output_pin caselight": {"pwm": True, "scale": 1.0},
    "output_pin relay": {"pwm": False, "scale": 1.0},
    "output_pin _beeper": {"pwm": True, "scale": 1.0},
    "screws_tilt_adjust": dict(SCREWS),
    "bed_screws": dict(SCREWS),
    "stepper_z": {"position_endstop": 0.0},
}
# What SCREWS_TILT_CALCULATE reports, screw1 being the reference.
SCREWS_TILT = {
    "screw1": {"z": 2.481, "sign": "CW", "adjust": "00:00", "is_base": True},
    "screw2": {"z": 2.532, "sign": "CW", "adjust": "00:18", "is_base": False},
    "screw3": {"z": 2.317, "sign": "CCW", "adjust": "01:02", "is_base": False},
    "screw4": {"z": 2.469, "sign": "CW", "adjust": "00:04", "is_base": False},
}


def fake_mesh(n: int = 7, tilt: float = 0.0) -> list[list[float]]:
    """A slightly warped bed: a bowl, a tilt towards the back and a ripple."""
    return [[round(0.09 * ((x - (n - 1) / 2) / (n / 2)) ** 2 - (0.06 + tilt) * y / (n - 1)
                   + 0.015 * math.sin(x * 1.3 + y), 4) for x in range(n)] for y in range(n)]


MESH_PARAMS = {"min_x": 20.0, "max_x": 210.0, "min_y": 20.0, "max_y": 210.0, "x_count": 7, "y_count": 7}
# A macro prompt, as a filament runout macro might show it.
PROMPT_DEMO = [
    "action:prompt_begin Filament runout",
    "action:prompt_text The filament ran out at layer 42. Load new filament, then resume.",
    "action:prompt_button Load filament|M117 Loading filament",
    "action:prompt_button Unload filament|M117 Unloading filament",
    "action:prompt_footer_button Resume|RESPOND TYPE=command MSG=action:prompt_end|primary",
    "action:prompt_footer_button Cancel print|RESPOND TYPE=command MSG=action:prompt_end|error",
    "action:prompt_show",
]
# Seconds these take on the fake printer.
SLOW = {"BED_MESH_CALIBRATE": 3.0, "SCREWS_TILT_CALCULATE": 2.0}
# Moonraker [power] devices. "printer" behaves like the printer's supply:
# off makes the MCU unreachable, on brings Klipper back.
POWER = {
    "printer": {"device": "printer", "status": "on", "locked_while_printing": True, "type": "gpio"},
    "lights": {"device": "lights", "status": "off", "locked_while_printing": False,
               "type": "tplink_smartplug"},
}
MACROS = ["PRINT_START", "PRINT_END", "LOAD_FILAMENT", "UNLOAD_FILAMENT", "BED_MESH_CALIBRATE",
          "_HOMING_VARS", "PARK", "PROMPT_DEMO"]


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
        self._probe_past: list[float] = []       # TESTZ heights tried, like Klipper's
        self._probe_kind = ""
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
            "motion_report": {"live_velocity": 0.0, "live_extruder_velocity": 0.0},
            "idle_timeout": {"state": "Idle"},
            "heaters": {"available_heaters": ["heater_bed", "extruder"],
                        "available_sensors": ["heater_bed", *SENSORS, "extruder"]},
            **{name: ({"temperature": temp, "target": 45.0, "speed": 0.3}
                      if name.startswith("temperature_fan") else {"temperature": temp})
               for name, temp in SENSORS.items()},
            **{name: dict(status) for name, status in DEVICES.items()},
            "configfile": {"settings": CONFIG_SETTINGS, "save_config_pending": False,
                           "save_config_pending_items": {}},
            "probe": {"name": "probe", "last_z_result": 0.0},
            "manual_probe": {"is_active": False, "z_position": None, "z_position_lower": None,
                             "z_position_upper": None},
            "bed_screws": {"is_active": False, "state": None, "current_screw": 0, "accepted_screws": 0},
            "screws_tilt_adjust": {"error": False, "max_deviation": None, "results": {}},
            "bed_mesh": {"profile_name": "", "mesh_matrix": [[]], "mesh_min": [0.0, 0.0],
                         "mesh_max": [0.0, 0.0],
                         "profiles": {"default": {"points": fake_mesh(), "mesh_params": MESH_PARAMS},
                                      "pei_sheet": {"points": fake_mesh(tilt=0.04),
                                                    "mesh_params": MESH_PARAMS}}},
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
            upd["gcode_move"] = {"gcode_position": [110.0, 110.0, round(0.2 + progress * 47.8, 2), 0.0]}
            wobble = math.sin(dur * 7)
            upd["motion_report"] = {"live_velocity": round(140 + 60 * wobble, 1),
                                    "live_extruder_velocity": round(2.4 + 1.2 * wobble, 3)}
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
                        "layer_height": 0.2, "first_layer_height": 0.2, "object_height": 48.0,
                        "layer_count": 240, "slicer": "OrcaSlicer",
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
                cmd = line.split()[0].upper() if line.strip() else ""
                if cmd in SLOW:
                    await asyncio.sleep(SLOW[cmd] / self.speedup)
                self._gcode(line.strip())
        except RPCError as e:
            # Klipper also broadcasts the error as a gcode response.
            self._store("response", f"!! {e}")
            self.on_gcode_response(f"!! {e}")
            raise
        return "ok"

    def _respond(self, text: str) -> None:
        self._store("response", text)
        self.on_gcode_response(text)

    def _need_homed(self) -> None:
        if not set(self.printer.homed_axes) >= set("xyz"):
            raise RPCError("Must home axis first: 0.000 0.000 0.000 [0.000]", 400)

    def _probe_status(self, z: float) -> None:
        """Klipper's report_z_status: the nearest heights tried below and above."""
        past = self._probe_past
        i = bisect.bisect_left(past, z)
        j = i + 1 if i < len(past) and past[i] == z else i
        self.printer.update({"manual_probe": {
            "is_active": True, "z_position": z,
            "z_position_lower": past[i - 1] if i > 0 else None,
            "z_position_upper": past[j] if j < len(past) else None}})

    def _testz(self, req: str) -> None:
        if not self.printer.get("manual_probe", "is_active"):
            raise RPCError('Unknown command:"TESTZ"', 400)
        z = self.printer.get("manual_probe", "z_position")
        past = self._probe_past
        i = bisect.bisect_left(past, z)
        if i >= len(past) or past[i] != z:
            past.insert(i, z)
        if req in ("+", "++"):
            check = past[i + 1] if i < len(past) - 1 else 1e12
            nxt = min((check + z) / 2 if req == "+" else check, z + 0.2)
        elif req in ("-", "--"):
            check = past[i - 1] if i > 0 else -1e12
            nxt = max((check + z) / 2 if req == "-" else check, z - 0.2)
        else:
            nxt = z + float(req)
        self._probe_status(round(nxt, 4))

    def _probe_finish(self, accept: bool) -> None:
        z = self.printer.get("manual_probe", "z_position") or 0.0
        self.printer.update({"manual_probe": {"is_active": False, "z_position": None,
                                              "z_position_lower": None, "z_position_upper": None}})
        if not accept or self._probe_kind == "MANUAL_PROBE":
            return
        section, option = (("probe", "z_offset") if self._probe_kind == "PROBE_CALIBRATE"
                           else ("stepper_z", "position_endstop"))
        self.printer.update({"configfile": {"save_config_pending": True,
                                            "save_config_pending_items": {section: {option: f"{z:.3f}"}}}})
        self._respond(f"// {section}: {option}: {z:.3f}\n// The SAVE_CONFIG command will update "
                      "the printer config file\n// with the above and restart the printer.")

    def _bed_screws(self, cmd: str) -> None:
        """Klipper's bed_screws: ACCEPT/ADJUSTED walk the screws, a coarse pass
        until all four are accepted in a row, then a fine pass."""
        bs = dict(self.printer.status["bed_screws"])
        count = 4
        if cmd == "ABORT":
            bs = {"is_active": False, "state": None, "current_screw": 0, "accepted_screws": 0}
        else:
            bs["accepted_screws"] = bs["accepted_screws"] + 1 if cmd == "ACCEPT" else 0
            if bs["current_screw"] + 1 < count and bs["accepted_screws"] < count:
                bs["current_screw"] += 1
            elif bs["accepted_screws"] < count:
                bs["state"], bs["current_screw"] = "adjust", 0
            elif bs["state"] == "adjust":
                bs.update(state="fine", current_screw=0, accepted_screws=0)
            else:
                bs = {"is_active": False, "state": None, "current_screw": 0, "accepted_screws": 0}
                self._respond("// Bed screws tool completed successfully")
        self.printer.update({"bed_screws": bs})

    def _load_mesh(self, name: str) -> None:
        profiles = self.printer.get("bed_mesh", "profiles", {})
        if name not in profiles:
            raise RPCError(f"bed_mesh: Unknown profile [{name}]", 400)
        mp = profiles[name]["mesh_params"]
        self.printer.update({"bed_mesh": {"profile_name": name, "mesh_matrix": profiles[name]["points"],
                                          "mesh_min": [mp["min_x"], mp["min_y"]],
                                          "mesh_max": [mp["max_x"], mp["max_y"]]}})

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
        elif cmd == "RESPOND":
            m = re.search(r'MSG="([^"]*)"|MSG=(.*)$', line, re.I)
            kind = words.get("TYPE", "ECHO").lower()
            prefix = {"command": "//", "error": "!!"}.get(kind, "echo:")
            self._respond(f"{prefix} {(m.group(1) or m.group(2) or '').strip() if m else ''}")
        elif cmd == "PROMPT_DEMO":
            for message in PROMPT_DEMO:
                self._respond(f"// {message}")
        elif cmd in ("PROBE_CALIBRATE", "Z_ENDSTOP_CALIBRATE", "MANUAL_PROBE"):
            self._need_homed()
            if pr.get("manual_probe", "is_active"):
                raise RPCError("Already in a manual Z probe. Use ABORT to abort it.", 400)
            self._probe_kind, self._probe_past = cmd, []
            self._probe_status(5.0)
        elif cmd == "TESTZ":
            self._testz(line.partition("=")[2].strip())
        elif cmd in ("ACCEPT", "ADJUSTED", "ABORT"):
            if pr.get("manual_probe", "is_active"):
                if cmd == "ADJUSTED":
                    raise RPCError('Unknown command:"ADJUSTED"', 400)
                self._probe_finish(cmd == "ACCEPT")
            elif pr.get("bed_screws", "is_active"):
                self._bed_screws(cmd)
            else:
                raise RPCError(f'Unknown command:"{cmd}"', 400)
        elif cmd == "BED_SCREWS_ADJUST":
            self._need_homed()
            pr.update({"bed_screws": {"is_active": True, "state": "adjust", "current_screw": 0,
                                      "accepted_screws": 0}})
        elif cmd == "SCREWS_TILT_CALCULATE":
            self._need_homed()
            pr.update({"screws_tilt_adjust": {"error": False, "results": dict(SCREWS_TILT)}})
        elif cmd == "BED_MESH_CALIBRATE":
            self._need_homed()
            profiles = dict(pr.get("bed_mesh", "profiles", {}))
            profiles["default"] = {"points": fake_mesh(tilt=0.01), "mesh_params": MESH_PARAMS}
            pr.update({"bed_mesh": {"profiles": profiles}})
            self._load_mesh("default")
            pr.update({"configfile": {"save_config_pending": True,
                                      "save_config_pending_items": {"bed_mesh default": {}}}})
        elif cmd == "BED_MESH_PROFILE" and "LOAD" in words:
            self._load_mesh(line.partition("=")[2].strip())
        elif cmd == "BED_MESH_CLEAR":
            pr.update({"bed_mesh": {"profile_name": "", "mesh_matrix": [[]]}})
        elif cmd == "M117":
            pr.update({"display_status": {"message": line[4:].strip() or None}})
        elif cmd == "M84":
            pr.update({"toolhead": {"homed_axes": ""}})
        elif cmd == "SET_FAN_SPEED":
            pr.update({f"fan_generic {words['FAN'].lower()}": {"speed": float(words["SPEED"])}})
        elif cmd == "SET_PIN":
            pr.update({f"output_pin {words['PIN'].lower()}": {"value": float(words["VALUE"])}})
        elif cmd in ("Z_OFFSET_APPLY_PROBE", "Z_OFFSET_APPLY_ENDSTOP"):
            offset = round(2.0 - pr.z_offset, 3)          # probe z_offset 2.000 before
            section = "probe" if cmd.endswith("PROBE") else "stepper_z"
            pr.update({"configfile": {"save_config_pending": True,
                                      "save_config_pending_items": {section: {"z_offset": f"{offset:.3f}"}}}})
            self._store("response", f"// {section}: z_offset: {offset:.3f}\n// The SAVE_CONFIG command "
                                    "will update the printer config file\n// and restart the printer.")
        elif cmd == "SAVE_CONFIG":
            if pr.job_active:
                raise RPCError("SAVE_CONFIG is not allowed while printing", 400)
            asyncio.ensure_future(self._restart())
        elif cmd == "SET_LED":
            name = next(o for o in pr.status if o.split()[0] in ("led", "neopixel")
                        and o.split()[-1].upper() == words["LED"])
            rgb = [float(words.get(c, 0)) for c in ("RED", "GREEN", "BLUE")]
            pr.update({name: {"color_data": [rgb + [0.0]]}})
