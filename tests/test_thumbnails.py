import asyncio
import io

from PIL import Image

from dwinui import protocol as p
from dwinui import thumbnails
from dwinui.config import Config
from dwinui.sim.harness import Harness
from dwinui.ui.screens.dashboard import DashboardScreen
from dwinui.ui.screens.files import THUMB, PrintPreview


def test_pick_prefers_smallest_big_enough():
    meta = {"filename": "sub/part.gcode", "thumbnails": [
        {"width": 32, "relative_path": ".thumbs/p-32.png"},
        {"width": 400, "relative_path": ".thumbs/p-400.png"},
        {"width": 300, "relative_path": ".thumbs/p-300.png"}]}
    assert thumbnails.pick(meta, 180) == "sub/.thumbs/p-300.png"
    meta["thumbnails"] = meta["thumbnails"][:1]
    assert thumbnails.pick(meta, 180) == "sub/.thumbs/p-32.png"     # best available
    assert thumbnails.pick({}, 180) is None


def test_to_jpeg_is_baseline_square_and_tail_free():
    png = io.BytesIO()
    Image.new("RGBA", (300, 200), (255, 0, 0, 128)).save(png, "PNG")
    jpeg = thumbnails.to_jpeg(png.getvalue(), 180, (30, 30, 30))
    img = Image.open(io.BytesIO(jpeg))
    assert img.format == "JPEG" and img.size == (180, 180)
    assert not img.info.get("progressive") and p.TAIL not in jpeg


def test_preview_dialog_shows_thumbnail_and_prints():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.click()
            await h.click()                       # Files
            await h.settle(0.2)
            await h.click()                       # newest file -> preview
            await h.settle(0.5)
            dialog = h.app.top
            assert isinstance(dialog, PrintPreview) and dialog.thumb.jpeg
            x, y = dialog.thumb.x + THUMB // 2, dialog.thumb.y + THUMB // 2
            r, g, b = h.link.canvas.screen.getpixel((x, y))
            assert g > r and g > b                # the green fake part, drawn from SRAM
            await h.click()                       # Print is preselected
            await h.settle(0.3)
            assert h.printer.job_state == "printing"
    asyncio.run(go())


def test_preview_without_thumbnail_says_so():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            h.app.push(PrintPreview(h.app, "voron_cable_chain_x.gcode",
                                    await h.api.call("server.files.metadata",
                                                     filename="voron_cable_chain_x.gcode")))
            await h.settle(0.3)
            assert h.app.top.thumb.placeholder == "No preview" and not h.app.top.thumb.jpeg
            await h.back()
            assert isinstance(h.app.top, DashboardScreen)
    asyncio.run(go())
