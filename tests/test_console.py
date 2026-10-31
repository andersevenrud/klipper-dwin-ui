import asyncio

from dwinui.config import Config
from dwinui.sim.harness import Harness
from dwinui.ui import theme as t
from dwinui.ui.screens.blocker import BlockerScreen
from dwinui.ui.screens.console import ConsoleScreen, console_lines


def run(coro):
    asyncio.run(coro)


def test_formatting():
    cmd = console_lines("command", "SAVE_GCODE_STATE NAME=_dwinui_jog\nG91\nG1 X1.000 F3000\n"
                                   "RESTORE_GCODE_STATE NAME=_dwinui_jog")
    assert cmd == [("> G91", t.FG), ("> G1 X1.000 F3000", t.FG)]
    assert console_lines("response", "!! Must home axis first")[0][1] == t.ERROR
    assert console_lines("response", "// Klipper state: Ready")[0][1] == t.DIM
    long = console_lines("response", "!! " + "word " * 20)
    assert len(long) > 1 and all(len(text) <= 32 for text, _ in long)


def test_history_then_live_follow_and_hold():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            console = ConsoleScreen(h.app)
            h.app.push(console)
            await h.settle(0.3)
            texts = [text for text, _ in console.log.lines]
            assert texts[:3] == ["// Klipper state: Ready", "> G28", "// probe at 110.000,110.000 is"]
            for i in range(30):                           # more than a screen full; only
                h.app._gcode_response(f"// line {i}")    # responses arrive live
            await h.settle(0.2)
            screen = console.log
            assert screen.at_bottom                       # followed the new lines
            h.app._gcode_response("!! first error")
            assert screen.lines[-1] == ("!! first error", t.ERROR) and screen.at_bottom
            await h.rotate(-5)                            # scroll up: hold position
            top = screen.top
            h.app._gcode_response("!! second error")
            assert screen.top == top and not screen.at_bottom
            await h.click()                               # press leaves
            assert not isinstance(h.app.top, ConsoleScreen)
    run(go())


def test_console_over_the_not_ready_screen():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.api.call("printer.emergency_stop")
            await h.settle(0.3)
            blocker = h.app.blocker
            labels = [i.label() if callable(i.label) else i.label for i in blocker.items]
            blocker.list.selected = labels.index("Console")
            await h.click()
            await h.settle(0.2)
            assert isinstance(h.app.top, ConsoleScreen)   # visible over the blocker
            await h.back()
            assert isinstance(h.app.top, BlockerScreen)
            await h.click()                               # open it again, then Klipper recovers
            await h.settle(0.2)
            await h.api.call("printer.firmware_restart")
            await h.settle(2.0)
            assert h.app.blocker is None and h.app.over_blocker == []
            assert not isinstance(h.app.top, ConsoleScreen)
    run(go())
