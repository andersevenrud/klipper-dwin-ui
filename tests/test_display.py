import asyncio

from dwinui import protocol as p
from dwinui.display import Display
from dwinui.sim.link import SimLink


def test_render_cache_skips_unchanged_frames():
    d = Display(SimLink())
    frames = [p.text(0, 0, "x")]
    assert d.draw("a", (0, 0, 9, 19), frames)
    assert not d.draw("a", (0, 0, 9, 19), list(frames))
    assert d.draw("a", (0, 0, 9, 19), [p.text(0, 0, "y")])


def test_invalidate_forces_redraw_of_overlapping_keys():
    d = Display(SimLink())
    d.draw("top", (0, 0, 50, 10), [p.fill(1, 0, 0, 50, 10)])
    d.draw("bottom", (0, 100, 50, 110), [p.fill(1, 0, 100, 50, 110)])
    d.invalidate((0, 5, 271, 20))
    assert d.cached_keys() == {"bottom"}


def test_handshake_and_flush():
    async def run():
        link = SimLink()
        d = Display(link)
        await d.handshake(attempts=1)
        d.draw("a", (0, 0, 1, 1), [p.fill(0xFFFF, 0, 0, 1, 1)])
        n = await d.flush()
        assert n > 0 and link.canvas.updates == 1
        assert await d.flush() == 0
    asyncio.run(run())
