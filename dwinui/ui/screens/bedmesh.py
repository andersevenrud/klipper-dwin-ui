"""Bed mesh: a heatmap of the loaded mesh (or a saved profile), calibrate,
load a profile, clear.

The mesh is fetched when the screen opens and after each command, not
subscribed: the matrix is large and rarely changes.
"""

from __future__ import annotations

import asyncio

from ... import protocol as p
from ... import thumbnails
from .. import theme as t
from ..screen import Item, ListScreen
from ..widgets import Text, Thumbnail
from .calibrate import offer_save

SIZE = 160
FIELDS = ["profile_name", "mesh_matrix", "profiles", "mesh_min", "mesh_max"]
# Low to high, as in Mainsail's heatmap.
COLORS = [(33, 150, 243), (0, 188, 212), (76, 175, 80), (255, 235, 59), (244, 67, 54)]


def pick_mesh(bed_mesh: dict) -> tuple[str, bool, list[list[float]], tuple] | None:
    """(name, loaded, matrix, (min_x, min_y, max_x, max_y)) of the loaded mesh,
    else of the "default" (or first) saved profile; None without any."""
    matrix = bed_mesh.get("mesh_matrix") or []
    if bed_mesh.get("profile_name") and matrix and matrix[0]:
        lo, hi = bed_mesh.get("mesh_min") or (0, 0), bed_mesh.get("mesh_max") or (1, 1)
        return bed_mesh["profile_name"], True, matrix, (lo[0], lo[1], hi[0], hi[1])
    profiles = bed_mesh.get("profiles") or {}
    name = "default" if "default" in profiles else next(iter(profiles), None)
    if name is None or not (profiles[name].get("points") or [[]])[0]:
        return None
    mp = profiles[name].get("mesh_params", {})
    bounds = (mp.get("min_x", 0), mp.get("min_y", 0), mp.get("max_x", 1), mp.get("max_y", 1))
    return name, False, profiles[name]["points"], bounds


def heatmap(matrix: list[list[float]], bounds: tuple, size: int, bg: tuple[int, int, int]) -> bytes:
    """Rows of Z from the front (min Y) back; drawn with the front at the bottom."""
    from PIL import Image

    lo = min(min(row) for row in matrix)
    hi = max(max(row) for row in matrix)
    span = (hi - lo) or 1.0

    def color(z: float) -> tuple[int, int, int]:
        f = (z - lo) / span * (len(COLORS) - 1)
        i = min(int(f), len(COLORS) - 2)
        a, b, k = COLORS[i], COLORS[i + 1], f - i
        return tuple(round(a[c] + (b[c] - a[c]) * k) for c in range(3))

    h, w = len(matrix), len(matrix[0])
    small = Image.new("RGB", (w, h))
    small.putdata([color(z) for row in reversed(matrix) for z in row])
    width, depth = max(1e-6, bounds[2] - bounds[0]), max(1e-6, bounds[3] - bounds[1])
    scale = size / max(width, depth)
    fw, fh = max(1, round(width * scale)), max(1, round(depth * scale))
    img = small.resize((fw, fh), Image.BILINEAR)
    canvas = Image.new("RGB", (size, size), bg)
    canvas.paste(img, ((size - fw) // 2, (size - fh) // 2))
    return thumbnails.encode(canvas)


class BedMeshScreen(ListScreen):
    title = "Bed mesh"

    def __init__(self, app) -> None:
        super().__init__(app)
        T = t.HEADER_H
        self.map = Thumbnail("bm.map", 6, T + 6, SIZE, t.BG)
        self.map.placeholder = "Loading..."
        x = 6 + SIZE + 10
        chars = (p.WIDTH - 6 - x) // p.FONTS[1][0]
        self.name = Text("bm.name", x, T + 6, chars, font=1)
        self.state = Text("bm.state", x, T + 24, chars, font=1, fg=t.DIM)
        self.stats = []
        for i, (label, color) in enumerate((("Range", t.FG), ("Max", t.HOT), ("Min", t.BED))):
            y = T + 56 + i * 38
            self.widgets.append(Text(f"bm.l{i}", x, y, chars, font=1, fg=t.DIM, value=label))
            value = Text(f"bm.v{i}", x, y + 16, chars, font=2, fg=color)
            self.stats.append(value)
        self.widgets += [self.map, self.name, self.state, *self.stats]
        self.set_list_top(T + SIZE + 16)
        self.mesh: dict = {}

    def on_enter(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        try:
            res = await self.api.call("printer.objects.query", objects={"bed_mesh": FIELDS})
        except Exception as e:  # noqa: BLE001
            self.map.placeholder = "No mesh"
            self.app.toast(str(e), "error")
            return
        self.mesh = res.get("status", {}).get("bed_mesh", {}) or {}
        self._build()
        picked = pick_mesh(self.mesh)
        if picked is None:
            self.map.jpeg = None
            self.map.placeholder = "No mesh"
            self.name.value = self.state.value = ""
            for value in self.stats:
                value.value = ""
            self.app.invalidate()
            return
        name, loaded, matrix, bounds = picked
        flat = [z for row in matrix for z in row]
        self.name.value = name
        self.state.value = "loaded" if loaded else "not loaded"
        self.stats[0].value = f"{max(flat) - min(flat):.3f}"
        self.stats[1].value = f"{max(flat):+.3f}"
        self.stats[2].value = f"{min(flat):+.3f}"
        self.map.jpeg = await asyncio.get_running_loop().run_in_executor(
            None, heatmap, matrix, bounds, SIZE, p.rgb565_to_rgb(t.BG))
        self.app.invalidate()

    def _build(self) -> None:
        active = self.mesh.get("profile_name") or ""
        idle = lambda: not self.printer.job_active  # noqa: E731
        items = [Item("Calibrate", action=self._calibrate, enabled=idle)]
        for name in self.mesh.get("profiles") or {}:
            items.append(Item(f"Load {name}", value=lambda n=name: "active" if n == active else "",
                              action=lambda n=name: self._run(f"BED_MESH_PROFILE LOAD={n}", f"Loaded {n}")))
        items.append(Item("Clear", action=lambda: self._run("BED_MESH_CLEAR", "Mesh cleared"),
                          enabled=lambda: bool(active)))
        self.set_items(items)

    def _run(self, script: str, done: str) -> None:
        async def run() -> None:
            try:
                await self.api.gcode(script)
            except Exception as e:  # noqa: BLE001
                self.app.toast(str(e), "error")
                return
            self.app.toast(done)
            await self._load()
        asyncio.ensure_future(run())

    def _calibrate(self) -> None:
        homed = set(self.printer.homed_axes) >= set("xyz")
        self.app.toast("Calibrating..." if homed else "Homing, then calibrating...", seconds=5)

        async def run() -> None:
            try:
                await self.api.gcode(("" if homed else "G28\n") + "BED_MESH_CALIBRATE")
            except Exception as e:  # noqa: BLE001
                self.app.toast(str(e), "error")
                return
            await self._load()
            if self.app.top is self:
                offer_save(self.app, "new mesh")
        asyncio.ensure_future(run())
