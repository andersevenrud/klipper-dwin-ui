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
    ("calibration/cube_20mm.gcode", 5 * 86400, 38 * 60, 1840.0),
    ("voron_cable_chain_x.gcode", 26 * 3600, 5 * 3600 + 2 * 60, 21400.0),
    ("filament_clip.gcode", 900, 9 * 60, 310.0),
]
# Mirrors a real printer's sensor set (Cartographer probe, CAN toolhead).
SENSORS = {
    "temperature_sensor Chamber": 21.0, "temperature_sensor Toolhead_MCU": 31.0,
    "temperature_fan Mainboard_MCU": 29.0, "temperature_sensor cartographer_coil": 24.0,
    "temperature_sensor Cartographer_MCU": 26.0, "temperature_fan Host_MCU": 36.0,
}
MACROS = ["PRINT_START", "PRINT_END", "LOAD_FILAMENT", "UNLOAD_FILAMENT", "BED_MESH_CALIBRATE",
          "_HOMING_VARS", "PARK"]


class FakeMoonraker:
    def __init__(self, printer: Printer, speedup: float = 1.0) -> None:
        self.printer = printer
        self.speedup = speedup
        self.on_error = lambda msg: None
        self.on_gcode_response = lambda line: None
        self.gcode_log: list[str] = []
        self.calls: list[tuple[str, dict]] = []
        self._print_total = 0.0

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
        }

    async def run(self) -> None:
        self.printer.connected = True
        self.printer.replace(self._initial())
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

    async def gcode(self, script: str) -> None:
        await self.call("printer.gcode.script", script=script)

    def gcode_fire(self, script: str) -> asyncio.Task:
        return self.fire("printer.gcode.script", script=script)

    async def _m_server_files_list(self, root: str = "gcodes") -> list[dict]:
        now = time.time()
        return [{"path": p, "modified": now - age, "size": 1_000_000, "permissions": "rw"}
                for p, age, _, _ in FILES]

    async def _m_server_files_metadata(self, filename: str) -> dict:
        for path, _, est, fil in FILES:
            if path == filename:
                return {"filename": path, "estimated_time": est, "filament_total": fil,
                        "layer_height": 0.2, "slicer": "OrcaSlicer", "slicer_version": "2.2.0"}
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
        return {"objects": ["webhooks", "extruder", "heater_bed"] +
                [f"gcode_macro {m}" for m in MACROS]}

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

    async def _m_machine_reboot(self) -> str:
        return "ok"

    async def _m_machine_shutdown(self) -> str:
        return "ok"

    async def _m_printer_gcode_script(self, script: str) -> str:
        for line in script.splitlines():
            self.gcode_log.append(line)
            self._gcode(line.strip())
        return "ok"

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
