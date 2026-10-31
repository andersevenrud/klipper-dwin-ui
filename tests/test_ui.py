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
            await h.click()                       # Print
            await h.settle(0.2)
            await h.rotate(2)                     # past Recent prints and the folder
            await h.click()                       # newest file -> preview (Print focused)
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


def test_redraws_after_panel_power_glitch():
    from dwinui import protocol as p
    from dwinui.ui import theme as t

    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            header = p.rgb565_to_rgb(t.HEADER_BG)
            assert h.link.canvas.screen.getpixel((200, 4)) == header
            h.link.power(False)                   # panel off, link stays up
            await h.settle(2.6)
            assert h.app.panel_lost
            h.link.power(True)                    # boots blank
            assert h.link.canvas.screen.getpixel((200, 4)) == (0, 0, 0)
            await h.settle(1.2)
            assert not h.app.panel_lost
            assert h.link.canvas.screen.getpixel((200, 4)) == header
    run(go())



def test_print_again_after_a_finished_print():
    from dwinui.ui.screen import Confirm
    from dwinui.ui.screens.printing import PrintingScreen

    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.api.call("printer.print.start", filename="filament_clip.gcode")
            await h.settle(0.3)
            h.printer.update({"print_stats": {"state": "cancelled"}})
            await h.settle(0.3)
            await h.click()
            dialog = h.app.top
            assert isinstance(dialog, Confirm)
            assert dialog.widgets[2].value == "Print cancelled"     # title follows the result
            await h.back()                                          # hold: just close it
            assert isinstance(h.app.top, PrintingScreen)
            await h.click()
            await h.rotate(1)                                       # -> Print again
            await h.click()
            await h.settle(0.4)
            assert h.printer.job_state == "printing"
            assert h.printer.get("print_stats", "filename") == "filament_clip.gcode"
            assert isinstance(h.app.top, PrintingScreen)
    run(go())


def test_printing_screen_layers_speed_flow_and_thumbnail():
    from dwinui.ui.screens.printing import PrintingScreen

    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.api.call("printer.print.start", filename="filament_clip.gcode")
            await h.settle(0.6)
            screen = h.app.top
            assert isinstance(screen, PrintingScreen)
            assert screen.thumb.jpeg                                # slicer thumbnail loaded
            h.printer.update({"gcode_move": {"gcode_position": [0, 0, 2.4, 0]},
                              "motion_report": {"live_velocity": 120.0,
                                                "live_extruder_velocity": 2.0}})
            screen.refresh()
            assert screen.layer.value == "12 / 240"                 # from Z and layer height
            assert screen.z.value == "2.40/48.00"
            assert screen.speed.value == "120mm/s"
            assert screen.flow.value == "5mm³/s"                    # 2 mm/s of 1.75 mm filament
            h.printer.update({"print_stats": {"info": {"current_layer": 30, "total_layer": 200}}})
            screen.refresh()
            assert screen.layer.value == "30 / 200"                 # the slicer's own numbers win
            h.printer.update({"print_stats": {"state": "complete", "info": {"current_layer": None, "total_layer": 200}}})
            screen.refresh()
            assert screen.layer.value == "200 / 200"
    run(go())


def test_full_redraw_keeps_the_screen_behind_a_dialog():
    from dwinui import protocol as p
    from dwinui.ui import theme as t
    from dwinui.ui.screen import Confirm

    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            h.app.push(Confirm(h.app, "Sure?", "", lambda: None))
            h.app.redraw()                                     # e.g. the panel came back
            await h.settle(0.3)
            header = h.link.canvas.snapshot().getpixel((2, 2))
            assert header == p.rgb565_to_rgb(t.HEADER_BG)
    run(go())
