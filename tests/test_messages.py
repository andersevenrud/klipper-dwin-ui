import asyncio

from dwinui.config import Config
from dwinui.sim.harness import Harness
from dwinui.ui.screens.printing import PrintingScreen


def run(coro):
    asyncio.run(coro)


def toast(h):
    return h.app._toast[0] if h.app._toast else None


def test_m117_pops_up():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            assert toast(h) is None
            await h.api.gcode("M117 Hello there")
            assert toast(h) == "Hello there"
            h.app._toast = None
            await h.api.gcode("M117")                     # clearing it is quiet
            assert toast(h) is None
            await h.api.gcode("M117 Hello there")          # the same text again is news
            assert toast(h) == "Hello there"
    run(go())


def test_old_message_and_printing_screen_stay_quiet():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            h.app.printer.set_state("disconnected", "")
            h.app.printer.update({"display_status": {"message": "Left over"}})
            h.app.printer.set_state("ready", "")           # reconnect with a message already set
            assert toast(h) is None
            await h.api.call("printer.print.start", filename="3DBenchy_PLA_31m20s.gcode")
            await h.settle(0.3)
            assert isinstance(h.app.top, PrintingScreen)
            await h.api.gcode("M117 Layer 2")              # shown in place instead
            assert toast(h) is None
    run(go())


def test_can_be_turned_off():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0, message_seconds=0)) as h:
            await h.settle(0.3)
            await h.api.gcode("M117 Hello there")
            assert toast(h) is None
    run(go())
