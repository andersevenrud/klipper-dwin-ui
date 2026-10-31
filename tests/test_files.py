import asyncio

from dwinui.config import Config
from dwinui.sim.harness import Harness
from dwinui.ui.screen import Confirm
from dwinui.ui.screens.files import FilesScreen, PrintPreview, RecentScreen


def run(coro):
    asyncio.run(coro)


def labels(screen):
    return [i.label for i in screen.items[1:]]              # skip "← Back"


def test_folders_and_recent_entry():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            top = FilesScreen(h.app)
            h.app.push(top)
            await h.settle(0.2)
            names = labels(top)
            assert names[0] == "↻ Recent prints" and names[1] == "calibration/"
            assert ".thumbs/" not in names and "cube_20mm.gcode" not in names
            await h.rotate(1)
            await h.click()                                   # into calibration/
            await h.settle(0.2)
            sub = h.app.top
            assert isinstance(sub, FilesScreen) and sub.header_title.value == "calibration"
            assert labels(sub) == ["temp_tower/", "cube_20mm.gcode"]
            await h.click()                                   # into temp_tower/
            await h.settle(0.2)
            assert labels(h.app.top) == ["tower_190-230.gcode"]
            await h.back()
            await h.back()
            assert h.app.top is top
    run(go())


def test_recent_prints_unique_with_results_and_limit():
    async def go():
        cfg = Config(long_press_ms=150, dim_timeout=0, recent_prints=3)
        async with Harness(cfg) as h:
            screen = RecentScreen(h.app)
            h.app.push(screen)
            await h.settle(0.2)
            rows = {i.label: (i.value(), i.is_enabled()) for i in screen.items[1:]}
            assert list(rows) == ["benchy.gcode", "calibration/cube_20mm.gcode",
                                  "old_part_since_deleted.gcode"]     # 3, repeats folded
            assert rows["benchy.gcode"][0].startswith("✓")           # latest job completed
            assert rows["calibration/cube_20mm.gcode"][0].startswith("×")
            assert rows["old_part_since_deleted.gcode"][1] is False  # file is gone
    run(go())


def test_recent_prints_can_be_hidden():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0, recent_prints=0)) as h:
            top = FilesScreen(h.app)
            h.app.push(top)
            await h.settle(0.2)
            assert "↻ Recent prints" not in labels(top)
    run(go())


def test_delete_from_preview():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            top = FilesScreen(h.app)
            h.app.push(top)
            await h.settle(0.2)
            top.list.selected = 1 + labels(top).index("filament_clip.gcode")
            await h.click()
            await h.settle(0.3)
            assert isinstance(h.app.top, PrintPreview)
            await h.rotate(-2)                                # Print -> Delete
            await h.click()
            assert isinstance(h.app.top, Confirm)
            await h.rotate(1)
            await h.click()                                   # confirm
            await h.settle(0.4)
            assert "filament_clip.gcode" not in h.api.files
            assert h.app.top is top and "filament_clip.gcode" not in labels(top)
    run(go())


def test_cannot_delete_the_file_being_printed():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.api.call("printer.print.start", filename="benchy.gcode")
            preview = PrintPreview(h.app, "benchy.gcode", {})
            h.app.push(preview)
            preview._delete()
            await h.settle(0.2)
            assert "benchy.gcode" in h.api.files
            assert h.app._toast and "being printed" in h.app._toast[0]
    run(go())
