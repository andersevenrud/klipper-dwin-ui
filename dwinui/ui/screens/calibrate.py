"""Calibration: a menu of what the printer supports, and the screens that
take over while Klipper waits for input.

PROBE_CALIBRATE and Z_ENDSTOP_CALIBRATE (and bed meshes without a probe) put
Klipper in manual-probe mode until ACCEPT or ABORT; BED_SCREWS_ADJUST walks
the screws until ACCEPT/ADJUSTED/ABORT. The app opens ManualProbeScreen and
BedScrewsScreen whenever those are active, whoever started them.
"""

from __future__ import annotations

import asyncio
import re

from ... import protocol as p
from .. import theme as t
from ..screen import Adjust, Confirm, Item, ListScreen
from ..widgets import Text

STEPS = (0.01, 0.05, 0.1, 1.0)
SCREW_KEY = re.compile(r"screw(\d+)$")


async def load_settings(api) -> dict:
    """Klipper's parsed config. Large, so asked for when needed, not subscribed."""
    try:
        res = await api.call("printer.objects.query", objects={"configfile": ["settings"]})
    except Exception:  # noqa: BLE001 - names fall back to "Screw 1"...
        return {}
    return res.get("status", {}).get("configfile", {}).get("settings", {}) or {}


def screw_names(section: dict) -> list[str]:
    """screw1, screw2... of a bed_screws / screws_tilt_adjust section, by number."""
    numbers = sorted(int(m.group(1)) for key in section if (m := SCREW_KEY.match(key)))
    return [str(section.get(f"screw{n}_name") or f"Screw {n}") for n in numbers]


def run_homed(screen, command: str, label: str) -> None:
    """Most of these need the printer homed; home first when it isn't."""
    homed = set(screen.printer.homed_axes) >= set("xyz")
    screen.app.toast(f"{label}..." if homed else f"Homing, then {label.lower()}...")
    screen.api.gcode_fire(command if homed else "G28\n" + command)


def offer_save(app, what: str) -> None:
    if app.printer.save_config_pending and not app.printer.job_active:
        app.push(Confirm(app, "Save config?",
                         f"Writes the {what} to printer.cfg and restarts Klipper.",
                         lambda: app.api.gcode_fire("SAVE_CONFIG"), ok="Save"))


class CalibrateScreen(ListScreen):
    title = "Calibrate"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.settings: dict = {}
        self._build()

    def on_enter(self) -> None:
        async def load() -> None:
            self.settings = await load_settings(self.api)
            self._build()
            self.app.invalidate()
        asyncio.ensure_future(load())

    def _build(self) -> None:
        from .bedmesh import BedMeshScreen

        objects = set(self.printer.objects)
        idle = lambda: not self.printer.job_active  # noqa: E731
        go = lambda cls: lambda: self.app.push(cls(self.app))  # noqa: E731
        run = lambda command, label: lambda: run_homed(self, command, label)  # noqa: E731
        items = []
        if "bed_mesh" in objects:
            items.append(Item("Bed mesh", action=go(BedMeshScreen)))
        if "screws_tilt_adjust" in objects:
            items.append(Item("Screws tilt", action=go(ScrewsTiltScreen)))
        if "bed_screws" in objects:
            items.append(Item("Bed screws", action=run("BED_SCREWS_ADJUST", "Bed screws"), enabled=idle))
        if "z_tilt" in objects:
            items.append(Item("Z tilt", action=run("Z_TILT_ADJUST", "Z tilt"), enabled=idle))
        if "quad_gantry_level" in objects:
            items.append(Item("Gantry level", action=run("QUAD_GANTRY_LEVEL", "Gantry level"),
                              enabled=idle))
        if self.printer.has_probe:
            items.append(Item("Probe Z offset", action=run("PROBE_CALIBRATE", "Probing"), enabled=idle))
        # Klipper only has Z_ENDSTOP_CALIBRATE with a physical Z endstop.
        if "position_endstop" in self.settings.get("stepper_z", {}):
            items.append(Item("Z endstop", action=run("Z_ENDSTOP_CALIBRATE", "Z endstop"),
                              enabled=idle))
        self.set_items(items)
        self.empty_text = "Nothing to calibrate"


class ScrewsTiltScreen(ListScreen):
    """SCREWS_TILT_CALCULATE and its last result: how far to turn each screw."""
    title = "Screws tilt"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.names: list[str] = []
        self._shown = None
        self.empty_text = ""

    def on_enter(self) -> None:
        async def load() -> None:
            self.names = screw_names((await load_settings(self.api)).get("screws_tilt_adjust", {}))
            self._shown = None
            self.app.invalidate()
        asyncio.ensure_future(load())

    def refresh(self) -> None:
        results = self.printer.get("screws_tilt_adjust", "results", {}) or {}
        key = (repr(results), tuple(self.names))
        if key != self._shown:
            self._shown = key
            items = [Item("Measure", action=lambda: run_homed(self, "SCREWS_TILT_CALCULATE", "Measuring"),
                          enabled=lambda: not self.printer.job_active)]
            for screw, result in sorted(results.items(), key=lambda kv: _screw_number(kv[0])):
                n = _screw_number(screw)
                name = self.names[n - 1] if 0 < n <= len(self.names) else f"Screw {n}"
                value = "base" if result.get("is_base") else f"{result.get('sign', '')} {result.get('adjust', '')}"
                items.append(Item(name, value=lambda v=value: v))
            self.set_items(items)
        super().refresh()


def _screw_number(key: str) -> int:
    m = re.search(r"\d+", key)
    return int(m.group()) if m else 0


class _Helper(ListScreen):
    """A takeover screen: info on top, the choices below. Holding the knob
    asks before aborting."""
    back_row = False
    INFO_H = 100

    def __init__(self, app) -> None:
        super().__init__(app)
        self.set_list_top(t.HEADER_H + self.INFO_H)

    def _gcode(self, script: str) -> None:
        self.api.gcode_fire(script)

    def on_back(self) -> None:
        if self.editing:
            self.editing = None
            return
        self.app.push(Confirm(self.app, "Abort?", "Stops the calibration without changing anything.",
                              lambda: self._gcode("ABORT"), ok="Abort", danger=True))


class ManualProbeScreen(_Helper):
    """The paper test: move the nozzle with the knob until a sheet of paper
    drags slightly, then accept."""
    title = "Paper test"

    def __init__(self, app) -> None:
        super().__init__(app)
        T = t.HEADER_H
        self.step = float(self.app.settings.get("probe_step", 0.05))
        if self.step not in STEPS:
            self.step = 0.05
        self._pending = 0.0
        self._worker: asyncio.Task | None = None
        self.z = Text("mp.z", 0, T + 8, p.WIDTH // p.FONTS[5][0], font=5, align="center")
        self.bounds = Text("mp.bounds", 0, T + 46, 34, font=1, fg=t.DIM, align="center")
        self.hint = Text("mp.hint", 0, T + 70, 34, font=1, fg=t.DIM, align="center",
                         value="Until a paper drags slightly")
        self.widgets += [self.z, self.bounds, self.hint]
        self.set_items([
            Item("Move Z", value=lambda: f"±{self.step:g}",
                 edit=Adjust(self._move, lambda: self._z_text())),
            Item("Step", value=lambda: f"{self.step:g} mm", action=self._next_step),
            Item("Halfway up", action=lambda: self._gcode("TESTZ Z=+")),
            Item("Halfway down", action=lambda: self._gcode("TESTZ Z=-")),
            Item("Accept", action=self._accept),
            Item("Abort", action=lambda: self._gcode("ABORT")),
        ])

    def _z_text(self) -> str:
        z = self.printer.get("manual_probe", "z_position")
        return f"{z:.3f}" if z is not None else "-"

    def refresh(self) -> None:
        super().refresh()
        mp = self.printer.status.get("manual_probe", {})
        self.z.value = "Z " + self._z_text()
        lower, upper = mp.get("z_position_lower"), mp.get("z_position_upper")
        fmt = lambda v: f"{v:.3f}" if v is not None else "?"  # noqa: E731
        self.bounds.value = f"between {fmt(lower)} and {fmt(upper)}" if lower is not None or \
            upper is not None else ""

    def _next_step(self) -> None:
        self.step = STEPS[(STEPS.index(self.step) + 1) % len(STEPS)]
        self.app.settings.set(self.api, "probe_step", self.step)

    def _move(self, delta: int) -> None:
        self._pending += delta * self.step
        if self._worker is None or self._worker.done():
            self._worker = asyncio.ensure_future(self._drain())

    async def _drain(self) -> None:
        while abs(self._pending) > 1e-9:
            move, self._pending = self._pending, 0.0
            try:
                await self.api.gcode(f"TESTZ Z={move:.3f}")
            except Exception as e:  # noqa: BLE001
                self._pending = 0.0
                self.app.toast(str(e), "error")
                return

    def _accept(self) -> None:
        async def run() -> None:
            try:
                await self.api.gcode("ACCEPT")
            except Exception as e:  # noqa: BLE001
                self.app.toast(str(e), "error")
                return
            for _ in range(30):                  # until the app has closed this screen
                if self not in self.app.stack:
                    break
                await asyncio.sleep(0.1)
            offer_save(self.app, "new Z position")
        asyncio.ensure_future(run())


class BedScrewsScreen(_Helper):
    """BED_SCREWS_ADJUST: Klipper parks the nozzle over each screw in turn."""
    title = "Bed screws"

    def __init__(self, app) -> None:
        super().__init__(app)
        T = t.HEADER_H
        self.names: list[str] = []
        self.screw = Text("bs.screw", 0, T + 8, p.WIDTH // p.FONTS[3][0], font=3, align="center")
        self.name = Text("bs.name", 0, T + 40, 34, font=1, align="center")
        self.stage = Text("bs.stage", 0, T + 64, 34, font=1, fg=t.DIM, align="center")
        self.widgets += [self.screw, self.name, self.stage]
        self.set_items([
            Item("Accept", value=lambda: "no change", action=lambda: self._gcode("ACCEPT")),
            Item("Adjusted", value=lambda: "turned it", action=lambda: self._gcode("ADJUSTED")),
            Item("Abort", action=lambda: self._gcode("ABORT")),
        ])

    def on_enter(self) -> None:
        async def load() -> None:
            self.names = screw_names((await load_settings(self.api)).get("bed_screws", {}))
            self.app.invalidate()
        asyncio.ensure_future(load())

    def refresh(self) -> None:
        super().refresh()
        bs = self.printer.status.get("bed_screws", {})
        index = int(bs.get("current_screw") or 0)
        count = len(self.names)
        self.screw.value = f"Screw {index + 1}" + (f" of {count}" if count else "")
        self.name.value = self.names[index] if index < count else ""
        stage = "Fine" if bs.get("state") == "fine" else "Coarse"
        self.stage.value = f"{stage} pass: until a paper drags"
