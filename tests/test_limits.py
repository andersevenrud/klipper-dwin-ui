import asyncio

from dwinui.config import Config
from dwinui.sim.harness import Harness
from dwinui.ui.screens.limits import LimitsScreen, PressureAdvanceScreen, RetractionScreen
from dwinui.ui.screens.tune import TuneScreen


def run(coro):
    asyncio.run(coro)


def labels(screen):
    return [i.label() if callable(i.label) else i.label for i in screen.items]


async def edit(h, screen, label, detents):
    screen.list.selected = labels(screen).index(label)
    await h.click()
    await h.rotate(detents)
    await h.click()
    await h.settle(0.1)


def test_pressure_advance():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            tune = TuneScreen(h.app)
            h.app.push(tune)
            assert labels(tune)[5:] == ["Pressure advance", "Limits", "Retraction"]
            tune.list.selected = 5
            await h.click()
            screen = h.app.top
            assert isinstance(screen, PressureAdvanceScreen)
            assert screen.items[1].value() == "0.040"
            await edit(h, screen, "Advance", 2)
            assert h.api.gcode_log[-1] == "SET_PRESSURE_ADVANCE ADVANCE=0.050"
            assert screen.items[1].value() == "0.050"
            await edit(h, screen, "Smooth time", -1)
            assert h.api.gcode_log[-1] == "SET_PRESSURE_ADVANCE SMOOTH_TIME=0.035"
    run(go())


def test_limits_and_retraction():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            limits = LimitsScreen(h.app)
            h.app.push(limits)
            assert labels(limits)[1:] == ["Velocity", "Acceleration", "Corner velocity", "Cruise ratio"]
            assert limits.items[1].value() == "300 mm/s"
            await edit(h, limits, "Acceleration", -2)
            assert h.api.gcode_log[-1] == "SET_VELOCITY_LIMIT ACCEL=2800"
            await edit(h, limits, "Cruise ratio", 1)
            assert h.api.gcode_log[-1] == "SET_VELOCITY_LIMIT MINIMUM_CRUISE_RATIO=0.55"
            assert h.app.printer.get("toolhead", "minimum_cruise_ratio") == 0.55
            await h.back()
            retraction = RetractionScreen(h.app)
            h.app.push(retraction)
            await edit(h, retraction, "Retract", 2)
            assert h.api.gcode_log[-1] == "SET_RETRACTION RETRACT_LENGTH=0.90"
            assert retraction.items[1].value() == "0.90 mm"
    run(go())


def test_older_klipper_has_accel_to_decel():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            toolhead = h.app.printer.status["toolhead"]
            del toolhead["minimum_cruise_ratio"]
            toolhead["max_accel_to_decel"] = 1500.0
            assert labels(LimitsScreen(h.app))[-1] == "Accel to decel"
    run(go())


def test_no_retraction_row_without_firmware_retraction():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            h.app.printer.objects.remove("firmware_retraction")
            assert "Retraction" not in labels(TuneScreen(h.app))
    run(go())
