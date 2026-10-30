import asyncio

from dwinui.config import Config
from dwinui.sim.harness import Harness
from dwinui.ui.screens.menu import MenuScreen
from dwinui.ui.screens.move import MoveScreen
from dwinui.ui.screens.dashboard import DashboardScreen
from dwinui.ui.screens.printing import PrintingScreen
from dwinui.ui.screens.temperature import TemperatureScreen


def run(coro):
    asyncio.run(coro)


def test_click_opens_menu_and_long_press_goes_back():
    async def go():
        async with Harness() as h:
            assert isinstance(h.app.top, DashboardScreen)
            await h.click()
            assert isinstance(h.app.top, MenuScreen)
            await h.back()
            assert isinstance(h.app.top, DashboardScreen)
    run(go())


def test_set_hotend_target_inline():
    async def go():
        async with Harness() as h:
            await h.click()
            await h.rotate(1)
            await h.click()                      # Temperature
            assert isinstance(h.app.top, TemperatureScreen)
            await h.click()                      # edit Hotend
            for _ in range(2):
                await h.rotate(1)                # +5 each
            await h.click()                      # commit
            await h.settle()
            assert "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=10" in h.api.gcode_log
            assert h.printer.temp("extruder")[1] == 10
    run(go())


def test_long_press_cancels_edit_without_commit():
    async def go():
        async with Harness() as h:
            h.app.push(TemperatureScreen(h.app))
            await h.settle()
            await h.click()
            await h.rotate(2)
            await h.back()
            assert h.api.gcode_log == []
            assert isinstance(h.app.top, TemperatureScreen)
    run(go())


def test_jog_detents_coalesce_into_few_moves():
    async def go():
        async with Harness() as h:
            screen = MoveScreen(h.app)
            h.app.push(screen)
            await h.api.gcode("G28")
            for _ in range(20):
                screen._jog("X", 1)              # 20 detents x 1mm, faster than moves complete
            await h.settle(0.5)
            moves = [line for line in h.api.gcode_log if line.startswith("G1 X")]
            assert 1 <= len(moves) < 20
            assert h.printer.position(0) == 20.0
    run(go())


def test_unhomed_jog_shows_error_toast():
    async def go():
        async with Harness() as h:
            screen = MoveScreen(h.app)
            h.app.push(screen)
            screen._jog("Y", 1)
            await h.settle(0.3)
            assert h.app._toast and "home" in h.app._toast[0].lower()
    run(go())


def test_estop_shows_blocker_and_firmware_restart_recovers():
    async def go():
        async with Harness() as h:
            await h.click()
            await h.api.call("printer.emergency_stop")
            await h.settle(0.3)
            assert h.app.blocker is not None
            await h.click()                       # Firmware restart
            await h.settle(2.0)
            assert h.app.blocker is None
            assert isinstance(h.app.top, DashboardScreen)
    run(go())


def test_idle_render_sends_nothing():
    async def go():
        async with Harness() as h:
            await h.settle(0.3)
            h.app.render()
            await h.app.display.flush()
            h.app.render()
            assert not h.app.display.pending
    run(go())


def test_print_start_from_files():
    async def go():
        async with Harness() as h:
            await h.click()
            await h.click()                       # Files
            await h.settle(0.2)
            await h.click()                       # newest file -> confirm (OK focused)
            await h.settle(0.2)
            await h.click()
            await h.settle(0.3)
            assert h.printer.job_state == "printing"
            assert isinstance(h.app.top, PrintingScreen)
    run(go())


def test_dimming_and_wake_swallows_input():
    async def go():
        cfg = Config(long_press_ms=150, dim_timeout=0.5, dim_brightness=10)
        async with Harness(cfg) as h:
            await h.settle(1.8)
            assert h.app.dimmed and h.link.canvas.brightness == 10
            await h.click()                       # wakes only
            await h.settle(0.1)
            assert not h.app.dimmed and h.link.canvas.brightness == 255
            assert isinstance(h.app.top, DashboardScreen)
    run(go())


def test_list_rows_keep_width_when_scrollbar_appears():
    from dwinui.ui.widgets import ListView, Row
    lv = ListView("l", (0, 40, 271, 430))
    lv.rows = [Row("a")]
    (_, short, _), *_ = lv.parts()
    lv.rows = [Row(str(i)) for i in range(30)]
    (_, long, _), *_ = lv.parts()
    assert short == long
