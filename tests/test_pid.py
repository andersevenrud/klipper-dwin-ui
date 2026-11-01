import asyncio

from dwinui.config import Config
from dwinui.sim.harness import Harness
from dwinui.ui.screen import Confirm
from dwinui.ui.screens.calibrate import CalibrateScreen
from dwinui.ui.screens.pid import PidScreen, parse_result, tuned_heater


def run(coro):
    asyncio.run(coro)


def labels(screen):
    return [i.label() if callable(i.label) else i.label for i in screen.items]


def test_parse():
    line = ("// PID parameters: pid_Kp=22.865 pid_Ki=1.306 pid_Kd=100.090\n"
            "// The SAVE_CONFIG command will update the printer config file")
    assert parse_result(line) == ("22.865", "1.306", "100.090")
    assert parse_result("// probe at 1,2 is z=3") is None
    assert tuned_heater({"heater_bed": {"pid_kp": "22.865"}}, "22.865") == "heater_bed"
    assert tuned_heater({}, "22.865") is None


def test_presets_and_custom():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            assert labels(PidScreen(h.app, "extruder"))[1:] == ["PLA", "PETG", "ABS", "Custom"]
            bed = PidScreen(h.app, "heater_bed")
            assert labels(bed)[1:] == ["PLA", "PETG", "ABS", "Custom"]
            assert bed.items[1].value() == "60°"
    run(go())


def test_tune_then_save_from_the_result():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            h.app.push(CalibrateScreen(h.app))
            await h.settle(0.3)
            screen = h.app.top
            screen.list.selected = labels(screen).index("PID hotend")
            await h.click()
            assert isinstance(h.app.top, PidScreen)
            await h.rotate(1)                                  # PETG, 235
            await h.click()
            assert isinstance(h.app.top, Confirm)              # warns before it starts
            await h.rotate(1)
            await h.click()
            await h.settle(0.3)
            assert h.api.gcode_log[-1] == "PID_CALIBRATE HEATER=extruder TARGET=235"
            assert len(h.app.stack) == 1                       # back home to watch the graph
            assert h.app.printer.temp("extruder")[1] == 235
            await h.settle(4.5)
            dialog = h.app.top
            assert isinstance(dialog, Confirm)
            assert any("Hotend: Kp 22.865" in getattr(w, "value", "") for w in dialog.widgets)
            await h.rotate(1)
            await h.click()
            await h.settle(0.2)
            assert h.api.gcode_log[-1] == "SAVE_CONFIG"
    run(go())


def test_result_of_a_tuning_started_elsewhere():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.gcode("PID_CALIBRATE HEATER=heater_bed TARGET=60")   # as if from Mainsail
            await h.settle(0.5)
            assert isinstance(h.app.top, Confirm)
            await h.click()                                    # Later
            assert not isinstance(h.app.top, Confirm)
            assert h.app.printer.save_config_pending          # still in System > Save config
    run(go())
