import asyncio

from dwinui.config import Config
from dwinui.sim.harness import Harness
from dwinui.ui.screen import Confirm
from dwinui.ui.screens.cancel import CancelScreen, object_labels
from dwinui.ui.screens.menu import MenuScreen


def run(coro):
    asyncio.run(coro)


def labels(screen):
    return [i.label() if callable(i.label) else i.label for i in screen.items]


def test_object_labels():
    assert object_labels(["3DBENCHY.STL_ID_0_COPY_0", "3DBENCHY.STL_ID_0_COPY_1", "SHAPE-BOX_ID_1_COPY_0",
                          "PART_A"]) == {
        "3DBENCHY.STL_ID_0_COPY_0": "3dbenchy", "3DBENCHY.STL_ID_0_COPY_1": "3dbenchy copy 1",
        "SHAPE-BOX_ID_1_COPY_0": "Shape-box", "PART_A": "Part a"}
    # Two different objects with the same name: keep Klipper's names.
    assert object_labels(["BOX_ID_0_COPY_0", "BOX_ID_1_COPY_0"]) == {
        "BOX_ID_0_COPY_0": "BOX_ID_0_COPY_0", "BOX_ID_1_COPY_0": "BOX_ID_1_COPY_0"}


async def cancel_tile(h):
    await h.click()                                       # printing screen -> menu
    menu = h.app.top
    assert isinstance(menu, MenuScreen)
    menu.selected = [e.label for e in menu.entries].index("Cancel")
    await h.click()


def test_one_object_keeps_the_dialog():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.call("printer.print.start", filename="3DBenchy_PLA_31m20s.gcode")
            await h.settle(0.3)
            await cancel_tile(h)
            assert isinstance(h.app.top, Confirm)
    run(go())


def test_exclude_an_object_then_cancel_the_rest():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.call("printer.print.start", filename="benchy.gcode")
            await h.settle(0.6)
            await cancel_tile(h)
            screen = h.app.top
            assert isinstance(screen, CancelScreen)
            assert labels(screen) == ["← Back", "Whole print", "3dbenchy", "3dbenchy copy 1", "Shape-box",
                                      "Filament clip"]
            assert screen.list.selected == 0                   # Back: a double press does nothing
            await h.click()
            assert isinstance(h.app.top, MenuScreen)           # back where it came from
            assert h.app.printer.job_state == "printing"
            await h.click()                                    # Cancel tile again
            screen = h.app.top
            await h.rotate(4)                                  # Shape-box
            await h.click()
            await h.settle(0.2)
            assert h.api.gcode_log[-1] == "EXCLUDE_OBJECT NAME=SHAPE-BOX_ID_1_COPY_0"
            assert screen.items[4].value() == "excluded" and not screen.items[4].is_enabled()
            assert h.app.printer.job_state == "printing"
            await h.rotate(-3)                                 # Whole print
            await h.click()
            await h.settle(0.2)
            assert h.app.printer.job_state == "cancelled"
    run(go())


def test_printing_screen_shows_the_current_object():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.call("printer.print.start", filename="benchy.gcode")
            await h.settle(0.6)
            screen = h.app.top
            assert screen.hint.value == "Object 1/4: 3dbenchy"
            await h.api.gcode("EXCLUDE_OBJECT NAME=3DBENCHY.STL_ID_0_COPY_0")
            await h.settle(0.6)
            assert screen.hint.value.startswith("Object 2/4")
    run(go())


def test_no_object_line_for_a_single_object():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.call("printer.print.start", filename="3DBenchy_PLA_31m20s.gcode")
            await h.settle(0.6)
            assert h.app.top.hint.value == ""
    run(go())
