import asyncio

from dwinui.config import Config
from dwinui.sim.harness import Harness
from dwinui.ui.screen import Confirm
from dwinui.ui.screens.bedmesh import BedMeshScreen, heatmap, pick_mesh
from dwinui.ui.screens.calibrate import (BedScrewsScreen, CalibrateScreen, ManualProbeScreen,
                                         ScrewsTiltScreen, screw_names)


def run(coro):
    asyncio.run(coro)


def labels(screen):
    return [i.label() if callable(i.label) else i.label for i in screen.items]


def test_screw_names():
    assert screw_names({"screw2": [1, 2], "screw1": [0, 0], "screw1_name": "front left",
                        "screw10": [5, 5], "speed": 50}) == ["front left", "Screw 2", "Screw 10"]


def test_pick_mesh_and_heatmap():
    profile = {"points": [[0.0, 0.1], [0.2, 0.3]], "mesh_params": {"min_x": 0, "max_x": 200,
                                                                  "min_y": 0, "max_y": 100}}
    assert pick_mesh({"profile_name": "", "mesh_matrix": [[]], "profiles": {}}) is None
    name, loaded, matrix, bounds = pick_mesh({"profile_name": "", "mesh_matrix": [[]],
                                              "profiles": {"pei": profile}})
    assert (name, loaded, bounds) == ("pei", False, (0, 0, 200, 100))
    loaded_mesh = pick_mesh({"profile_name": "pei", "mesh_matrix": [[1.0]], "mesh_min": [5, 5],
                             "mesh_max": [9, 9], "profiles": {"pei": profile}})
    assert loaded_mesh[1] and loaded_mesh[2] == [[1.0]]
    jpeg = heatmap(matrix, bounds, 64, (0, 0, 0))
    assert jpeg[:2] == b"\xff\xd8"
    heatmap([[0.0, 0.0], [0.0, 0.0]], bounds, 64, (0, 0, 0))   # flat bed: no division by zero


def test_calibrate_menu_lists_what_the_printer_has():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            h.app.push(CalibrateScreen(h.app))
            await h.settle(0.3)
            assert labels(h.app.top)[1:] == ["Bed mesh", "Screws tilt", "Bed screws", "Probe Z offset",
                                             "PID hotend", "PID bed", "Z endstop"]
    run(go())


def test_paper_test_takes_over_and_offers_save():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            h.app.push(CalibrateScreen(h.app))
            await h.settle(0.3)
            screen = h.app.top
            screen.list.selected = labels(screen).index("Probe Z offset")
            await h.click()                                   # homes first, then probes
            await h.settle(0.3)
            probe = h.app.top
            assert isinstance(probe, ManualProbeScreen)
            assert h.api.gcode_log[-2:] == ["G28", "PROBE_CALIBRATE"]
            await h.click()                                   # Move Z: knob moves by the step
            await h.rotate(-2)
            await h.settle(0.2)
            assert h.api.gcode_log[-1] == "TESTZ Z=-0.100"
            assert h.app.printer.get("manual_probe", "z_position") == 4.9
            await h.click()
            probe.list.selected = labels(probe).index("Accept")
            await h.click()
            await h.settle(0.5)
            assert isinstance(h.app.top, Confirm)             # SAVE_CONFIG?
            assert ManualProbeScreen not in [type(s) for s in h.app.stack]
    run(go())


def test_hold_asks_before_abort():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.gcode("G28\nPROBE_CALIBRATE")
            await h.settle(0.2)
            await h.back()
            assert isinstance(h.app.top, Confirm)
            await h.rotate(1)
            await h.click()
            await h.settle(0.3)
            assert h.api.gcode_log[-1] == "ABORT"
            assert not any(isinstance(s, (ManualProbeScreen, Confirm)) for s in h.app.stack)
    run(go())


def test_started_elsewhere_and_bed_screws():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.gcode("G28\nBED_SCREWS_ADJUST")         # as if from Mainsail
            await h.settle(0.5)
            screen = h.app.top
            assert isinstance(screen, BedScrewsScreen)
            assert screen.screw.value == "Screw 1 of 4" and screen.name.value == "front left"
            for _ in range(4):
                screen.list.selected = 0                        # Accept
                await h.click()
                await h.settle(0.1)
            assert screen.stage.value.startswith("Fine")
            for _ in range(4):
                screen.list.selected = 0
                await h.click()
                await h.settle(0.1)
            await h.settle(0.2)
            assert not isinstance(h.app.top, BedScrewsScreen)
    run(go())


def test_screws_tilt_results():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            screen = ScrewsTiltScreen(h.app)
            h.app.push(screen)
            await h.settle(0.3)
            assert labels(screen) == ["← Back", "Measure"]
            await h.click()
            await h.settle(2.5)                               # measuring takes 2 s here
            assert labels(screen)[2:] == ["front left", "front right", "rear right", "rear left"]
            assert screen.items[3].value() == "CW 00:18" and screen.items[2].value() == "base"
    run(go())


def test_bed_mesh_load_and_clear():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            screen = BedMeshScreen(h.app)
            h.app.push(screen)
            await h.settle(0.5)
            assert screen.state.value == "not loaded" and screen.map.jpeg
            screen.list.selected = labels(screen).index("Load pei_sheet")
            await h.click()
            await h.settle(0.5)
            assert screen.name.value == "pei_sheet" and screen.state.value == "loaded"
            screen.list.selected = labels(screen).index("Clear")
            await h.click()
            await h.settle(0.5)
            assert h.app.printer.get("bed_mesh", "profile_name") == ""
            assert screen.state.value == "not loaded"
    run(go())


def test_screws_tilt_results_from_elsewhere_pop_up():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.gcode("G28\nSCREWS_TILT_CALCULATE")     # as if from Mainsail
            await h.settle(0.3)
            assert isinstance(h.app.top, ScrewsTiltScreen)
            await h.back()
            await h.settle(0.3)
            assert not isinstance(h.app.top, ScrewsTiltScreen)  # stays closed: nothing new
    run(go())


def test_old_screws_tilt_results_stay_quiet():
    async def go():
        h = Harness(Config(long_press_ms=150, dim_timeout=0))
        async with h:
            h.app.printer.set_state("disconnected", "")
            await h.settle(0.2)
            h.app.printer.update({"screws_tilt_adjust": {"results": {"screw1": {"is_base": True}}}})
            h.app.printer.set_state("ready", "")              # reconnect: results already there
            await h.settle(0.3)
            assert not isinstance(h.app.top, ScrewsTiltScreen)
    run(go())
