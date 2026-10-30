"""The scrolled graph must match a full redraw pixel for pixel."""

import asyncio

from PIL import ImageChops

from dwinui import protocol as p
from dwinui.config import Config
from dwinui.history import TempHistory
from dwinui.sim.harness import Harness
from dwinui.sim.renderer import Canvas
from dwinui.ui import theme as t
from dwinui.ui.graph import MIN_SPAN, Graph, nice_range
from dwinui.ui.screens.dashboard import DashboardScreen
from dwinui.ui.screens.printing import PrintingScreen

AREA = (0, 100, 263, 275)
COLORS = {"extruder": (t.HOT, t.HOT_DIM), "heater_bed": (t.BED, t.BED_DIM)}


def render(graph: Graph) -> Canvas:
    canvas = Canvas()
    (_, _, lazy), = graph.parts()
    for f in lazy.build():
        canvas.feed(f)
    return canvas


def test_incremental_scroll_matches_full_redraw():
    history = TempHistory(size=240)
    history.reset(list(COLORS))
    for i in range(150):
        history.append({"extruder": (22 + i, 200.0 if i > 40 else 0.0), "heater_bed": (22 + i / 3, 60.0)})
    graph = Graph("g", AREA, history, COLORS)
    live = render(graph)
    (_, _, lazy), = graph.parts()
    first_scale = lazy.sig[1]
    for i in range(150, 230):                 # stays inside the current scale
        history.append({"extruder": (min(200.0, 22 + i), 200.0), "heater_bed": (60.0, 60.0)})
        (_, _, lazy), = graph.parts()
        assert lazy.sig[1] == first_scale, "scale changed; test assumes a fixed scale"
        for f in graph.incremental(False):
            live.feed(f)
        live.feed(p.update())
    fresh = Graph("g", AREA, history, COLORS)
    full = render(fresh)
    full.feed(p.update())
    live.feed(p.update())
    crop = fresh.plot
    box = (crop[0], crop[1], crop[2] + 1, crop[3] + 1)
    assert ImageChops.difference(live.screen.crop(box), full.screen.crop(box)).getbbox() is None


def test_scroll_tick_is_small():
    history = TempHistory()
    history.reset(list(COLORS))
    for _ in range(50):
        history.append({"extruder": (200.0, 210.0), "heater_bed": (60.0, 60.0)})
    graph = Graph("g", AREA, history, COLORS)
    render(graph)
    history.append({"extruder": (201.0, 210.0), "heater_bed": (60.0, 60.0)})
    graph.parts()
    tick = b"".join(graph.incremental(False))
    assert 0 < len(tick) < 120


def test_rescale_triggers_full_redraw():
    history = TempHistory()
    history.reset(list(COLORS))
    history.append({"extruder": (50.0, 0.0), "heater_bed": (20.0, 0.0)})
    graph = Graph("g", AREA, history, COLORS)
    (_, _, before), = graph.parts()
    history.append({"extruder": (240.0, 0.0), "heater_bed": (20.0, 0.0)})
    (_, _, after), = graph.parts()
    assert before.sig != after.sig


def test_area_move_in_simulator():
    c = Canvas()
    c.feed(p.fill(0xFFFF, 10, 10, 10, 10) + p.area_move(p.MOVE_LEFT, 3, 0x0000, 0, 0, 20, 20) + p.update())
    assert c.screen.getpixel((7, 10)) == (255, 255, 255)
    assert c.screen.getpixel((10, 10)) == (0, 0, 0)


def test_job_lifecycle_switches_root_screen():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            assert isinstance(h.app.top, DashboardScreen)
            await h.click()                                   # menu open while a job starts elsewhere
            await h.api.call("printer.print.start", filename="benchy.gcode")
            await h.settle(0.3)
            assert isinstance(h.app.top, PrintingScreen)      # jumped to the job
            h.printer.update({"print_stats": {"state": "complete"}})
            await h.settle(0.3)
            assert isinstance(h.app.top, PrintingScreen)      # result stays visible
            await h.click()
            await h.settle(0.3)
            assert isinstance(h.app.top, DashboardScreen)
    asyncio.run(go())


def test_nice_range():
    assert nice_range(18.0, 61.0) == (10, 70, 10)      # cooling hotend + idle bed
    ymin, ymax, step = nice_range(18.6, 18.9)            # flat: centred, >= MIN_SPAN
    assert ymax - ymin >= MIN_SPAN and ymin <= 18.6 and ymax >= 18.9
    assert nice_range(20.0, 215.0) == (0, 250, 50)       # heating to 210


def test_dashboard_scrolls_sensors_with_heaters_pinned():
    async def go():
        cfg = Config(long_press_ms=150, dim_timeout=0, dashboard_rows=6)
        async with Harness(cfg) as h:
            screen = h.app.top
            assert isinstance(screen, DashboardScreen)
            first = screen.visible_sensors()
            assert first[:2] == ["extruder", "heater_bed"] and len(first) == 6
            await h.rotate(1)
            moved = screen.visible_sensors()
            assert moved[:2] == first[:2] and moved[2] == first[3]
            await h.rotate(10)                                # clamps at the end
            assert screen.visible_sensors()[-1] == screen.rest[-1]
            assert screen.hint.value.startswith("turn: scroll")
    asyncio.run(go())
