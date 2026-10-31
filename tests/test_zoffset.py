import asyncio

from dwinui.config import Config
from dwinui.sim.harness import Harness
from dwinui.ui.screen import Confirm
from dwinui.ui.screens.system import SystemScreen
from dwinui.ui.screens.tune import TuneScreen


def run(coro):
    asyncio.run(coro)


def item(screen, label):
    return next(i for i in screen.items if i.label == label)


async def babystep(h, mm):
    await h.api.gcode("G28")
    await h.api.gcode(f"SET_GCODE_OFFSET Z_ADJUST={mm} MOVE=1")


def test_save_when_idle_applies_and_offers_save_config():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            tune = TuneScreen(h.app)
            h.app.push(tune)
            assert not item(tune, "Save Z offset").is_enabled()     # nothing to save yet
            await babystep(h, -0.05)
            assert item(tune, "Save Z offset").is_enabled()
            item(tune, "Save Z offset").action()
            await h.settle(0.2)
            assert "Z_OFFSET_APPLY_PROBE" in h.api.gcode_log
            assert h.printer.save_config_pending
            assert isinstance(h.app.top, Confirm)
            await h.rotate(1)
            await h.click()                                         # Save
            await h.settle(0.2)
            assert "SAVE_CONFIG" in h.api.gcode_log
    run(go())


def test_during_a_print_only_applies():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.api.call("printer.print.start", filename="benchy.gcode")
            await babystep(h, 0.02)
            tune = TuneScreen(h.app)
            h.app.push(tune)
            item(tune, "Save Z offset").action()
            await h.settle(0.2)
            assert not isinstance(h.app.top, Confirm)
            assert "SAVE_CONFIG" not in h.api.gcode_log
            assert h.app._toast and "after the print" in h.app._toast[0]
            system = SystemScreen(h.app)
            assert item(system, "Save config").value() == "pending"
            assert not item(system, "Save config").is_enabled()     # would kill the print
    run(go())


def test_endstop_variant_without_a_probe():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            h.printer.objects = [o for o in h.printer.objects if o != "probe"]
            await babystep(h, 0.1)
            tune = TuneScreen(h.app)
            h.app.push(tune)
            item(tune, "Save Z offset").action()
            await h.settle(0.2)
            assert "Z_OFFSET_APPLY_ENDSTOP" in h.api.gcode_log
    run(go())


def test_system_save_config_row():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            system = SystemScreen(h.app)
            assert not item(system, "Save config").is_enabled()     # nothing pending
            h.printer.update({"configfile": {"save_config_pending": True}})
            assert item(system, "Save config").is_enabled()
            h.app.push(system)
            system.list.selected = system.items.index(item(system, "Save config"))
            await h.click()
            assert isinstance(h.app.top, Confirm)
            await h.rotate(1)
            await h.click()
            await h.settle(0.2)
            assert "SAVE_CONFIG" in h.api.gcode_log
    run(go())
