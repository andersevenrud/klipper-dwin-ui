import asyncio

from dwinui.config import Config
from dwinui.moonraker import Moonraker
from dwinui.printer import Printer
from dwinui.sim.harness import Harness
from dwinui.ui.screen import Confirm
from dwinui.ui.screens.power import PowerScreen
from dwinui.ui.screens.system import SystemScreen


def run(coro):
    asyncio.run(coro)


def test_printer_off_then_on_from_the_not_ready_screen():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.api.call("machine.device_power.post_device", device="printer", action="off")
            await h.settle(0.3)
            blocker = h.app.blocker
            assert blocker is not None
            labels = [i.label for i in blocker.items]
            # Only the printer's supply (locked while printing), not the lights plug;
            # restarts follow since Klipper is in error.
            assert labels == ["Power on: Printer", "Firmware restart", "Restart Klipper", "Console"]
            blocker.list.selected = labels.index("Power on: Printer")
            await h.click()
            await h.settle(2.2)
            assert h.printer.power["printer"]["status"] == "on"
            assert h.app.blocker is None and h.printer.ready
    run(go())


def test_power_screen_confirms_off_and_respects_print_lock():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            system = SystemScreen(h.app)
            assert "Power" in [i.label for i in system.items]
            screen = PowerScreen(h.app)
            h.app.push(screen)
            await h.settle()
            screen.list.selected = 1 + [i.label for i in screen.items[1:]].index("Lights")
            await h.click()                                   # off -> on: no question
            await h.settle(0.2)
            assert h.printer.power["lights"]["status"] == "on"
            await h.click()                                   # on -> off: confirm first
            assert isinstance(h.app.top, Confirm)
            await h.back()
            assert h.printer.power["lights"]["status"] == "on"
            await h.api.call("printer.print.start", filename="benchy.gcode")
            await h.settle(0.3)
            printer_item = next(i for i in screen.items if i.label == "Printer")
            assert not printer_item.is_enabled()              # locked while printing
    run(go())


def test_no_power_component_means_no_devices():
    async def go():
        printer = Printer()
        mr = Moonraker(printer)

        async def call(method, **params):
            if method == "machine.device_power.devices":
                from dwinui.moonraker import RPCError
                raise RPCError("Method not found", -32601)
            return {}
        mr.call = call
        await mr._load_power_devices()
        assert printer.power == {}
        mr._notify("notify_power_changed", [{"device": "psu", "status": "on"}])
        assert printer.power["psu"]["status"] == "on"
    run(go())


def test_power_cycle_from_a_fatal_error(monkeypatch):
    from dwinui.ui.screens import blocker as blocker_mod
    monkeypatch.setattr(blocker_mod, "CYCLE_OFF_SECONDS", 0.3)

    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.api.call("printer.emergency_stop")
            await h.settle(0.3)
            blocker = h.app.blocker
            labels = [i.label() if callable(i.label) else i.label for i in blocker.items]
            assert labels == ["Firmware restart", "Restart Klipper", "Power cycle: Printer", "Console"]
            blocker.list.selected = 2
            await h.click()                                   # first press only arms it
            assert h.printer.power["printer"]["status"] == "on"
            assert blocker.items[2].label() == "Press again to cycle"
            await h.click()
            await h.settle(0.15)
            assert h.printer.power["printer"]["status"] == "off"
            await h.settle(2.5)                               # back on, Klipper restarts
            assert h.printer.power["printer"]["status"] == "on"
            assert h.app.blocker is None and h.printer.ready
    run(go())


def test_supplies_fall_back_to_all_devices_when_none_locked():
    printer = Printer()
    printer.power = {"psu": {"status": "on"}, "lamp": {"status": "off"}}
    assert printer.printer_supplies() == ["lamp", "psu"]
    printer.power["psu"]["locked_while_printing"] = True
    assert printer.printer_supplies() == ["psu"]
