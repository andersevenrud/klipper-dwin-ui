import asyncio

from dwinui.config import Config
from dwinui.sim.harness import Harness
from dwinui.ui.screens.extrude import ExtrudeScreen, HeatScreen
from dwinui.ui.screens.printing import PrintingScreen


def run(coro):
    asyncio.run(coro)


def labels(screen):
    return [i.label() if callable(i.label) else i.label for i in screen.items]


def test_heat_preset_and_macros():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            screen = ExtrudeScreen(h.app)
            h.app.push(screen)
            await h.settle(0.1)
            assert labels(screen)[:5] == ["← Back", "Hotend", "Heat", "Load", "Unload"]
            screen.list.selected = 2
            await h.click()
            assert isinstance(h.app.top, HeatScreen)
            await h.rotate(1)                                  # PETG
            await h.click()
            assert h.app.top is screen
            assert h.api.gcode_log[-1] == "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=235"
            assert h.app.printer.temp("heater_bed")[1] == 0     # the bed is left alone
            screen.list.selected = 3
            await h.click()
            assert h.api.gcode_log[-1] == "LOAD_FILAMENT"
    run(go())


def test_macro_rows_only_when_the_macros_exist():
    async def go():
        cfg = Config(long_press_ms=150, dim_timeout=0, load_macro="FILAMENT_IN", unload_macro="")
        async with Harness(cfg) as h:
            await h.settle(0.3)
            screen = ExtrudeScreen(h.app)
            assert "Load" not in labels(screen) and "Unload" not in labels(screen)
    run(go())


def test_runout_pause_shows_the_reason_and_wakes():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.call("printer.print.start", filename="benchy.gcode")
            await h.settle(0.3)
            h.app.dimmed = True
            await h.api.gcode("RUNOUT_DEMO")
            await h.settle(0.3)
            screen = h.app.top
            assert isinstance(screen, PrintingScreen) and screen.header_title.value == "Paused"
            assert screen.message1.value == "Filament runout: Runout"
            assert not h.app.dimmed
            await h.api.gcode("LOAD_FILAMENT")
            await h.settle(0.3)
            assert screen.message1.value == ""                  # filament is back
    run(go())


def test_manual_pause_after_resuming_empty_is_not_a_runout():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.call("printer.print.start", filename="benchy.gcode")
            await h.settle(0.3)
            await h.api.gcode("RUNOUT_DEMO")
            await h.settle(0.3)
            screen = h.app.top
            assert screen.message1.value.startswith("Filament runout")
            await h.api.call("printer.print.resume")             # resumed without loading
            await h.settle(0.3)
            await h.api.call("printer.print.pause")
            await h.settle(0.3)
            assert screen.header_title.value == "Paused" and screen.message1.value == ""
    run(go())
