import asyncio

from dwinui.config import Config
from dwinui.printer import LIGHT_TYPES
from dwinui.sim.harness import Harness
from dwinui.ui.screens.devices import FansScreen, LightsScreen
from dwinui.ui.screens.menu import MenuScreen


def run(coro):
    asyncio.run(coro)


def labels(screen):
    return [i.label for i in screen.items[1:]]          # skip "← Back"


async def edit_row(h, screen, label, turns):
    """Select a row by label, open its editor, turn, commit."""
    screen.list.selected = 1 + labels(screen).index(label)
    await h.click()
    await h.rotate(turns)
    await h.click()
    await h.settle(0.2)


def test_fans_screen():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            screen = FansScreen(h.app)
            h.app.push(screen)
            await h.settle()
            assert labels(screen)[0] == "Part fan"
            assert {"Exhaust", "Hotend", "Electronics", "Host MCU"} <= set(labels(screen))
            await edit_row(h, screen, "Part fan", 2)            # 2 x 5%
            assert "M106 S26" in h.api.gcode_log
            await edit_row(h, screen, "Exhaust", 1)
            assert "SET_FAN_SPEED FAN=EXHAUST SPEED=0.05" in [g.upper() for g in h.api.gcode_log]
            hotend = screen.items[1 + labels(screen).index("Hotend")]
            assert hotend.edit is None and hotend.action is None   # Klipper runs it
    run(go())


def test_lights_screen():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            screen = LightsScreen(h.app)
            h.app.push(screen)
            await h.settle(0.3)
            assert labels(screen) == ["Caselight", "Relay", "Toolhead"]   # _beeper hidden
            await edit_row(h, screen, "Caselight", 2)
            assert h.printer.get("output_pin caselight", "value") == 0.1
            screen.list.selected = 1 + labels(screen).index("Relay")
            await h.click()                                     # on/off pin toggles
            await h.settle(0.2)
            assert h.printer.get("output_pin relay", "value") == 1.0
            await edit_row(h, screen, "Toolhead", 2)
            assert h.printer.get("neopixel toolhead", "color_data")[0][:3] == [0.1, 0.1, 0.1]
    run(go())


def test_lights_tile_only_with_lights():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.click()
            assert isinstance(h.app.top, MenuScreen)
            assert {"Fans", "Lights"} <= {e.label for e in h.app.top.entries}
            h.printer.objects = [o for o in h.printer.objects if o.split()[0] not in LIGHT_TYPES]
            await h.settle()
            assert "Lights" not in {e.label for e in h.app.top.entries}
    run(go())
