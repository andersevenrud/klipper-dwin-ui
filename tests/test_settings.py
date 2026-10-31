import asyncio

from dwinui.config import Config
from dwinui.sim.harness import Harness
from dwinui.ui.screens.extrude import ExtrudeScreen
from dwinui.ui.screens.move import MoveScreen


def run(coro):
    asyncio.run(coro)


def cfg():
    return Config(long_press_ms=150, dim_timeout=0, brightness=255)


def test_display_changes_survive_a_restart():
    db: dict = {}                                        # Moonraker's database, shared

    async def first():
        async with Harness(cfg(), db=db) as h:
            await h.app.set_brightness(120)
            move = MoveScreen(h.app)
            move._next_step()                            # 1 mm -> 10 mm
            extrude = ExtrudeScreen(h.app)
            extrude._set_length(25.0)
            await h.settle(0.2)
            assert db["dwinui"] == {"brightness": 120, "jog_step": 10.0, "extrude_length": 25.0}

    async def second():
        async with Harness(cfg(), db=db) as h:
            await h.settle(0.8)                          # settings load after connecting
            assert h.app.brightness == 120 and h.link.canvas.brightness == 120
            assert MoveScreen(h.app).step == 10.0
            assert ExtrudeScreen(h.app).length == 25.0

    run(first())
    run(second())


def test_reset_returns_to_the_config():
    db = {"dwinui": {"brightness": 100, "jog_step": 50.0}}

    async def go():
        async with Harness(cfg(), db=db) as h:
            await h.settle(0.8)
            assert h.app.brightness == 100
            await h.app.settings.reset(h.api)
            await h.settle(0.2)
            assert db["dwinui"] == {}
            assert h.app.brightness == 255 and h.link.canvas.brightness == 255
            assert MoveScreen(h.app).step == 1.0
    run(go())


def test_nothing_saved_uses_the_config():
    async def go():
        async with Harness(cfg()) as h:
            await h.settle(0.8)
            assert h.app.settings.loaded and h.app.settings.values == {}
            assert h.app.brightness == 255
    run(go())
