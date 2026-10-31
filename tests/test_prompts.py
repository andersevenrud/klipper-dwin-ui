import asyncio

from dwinui.config import Config
from dwinui.prompts import CLOSE, PromptTracker
from dwinui.sim.harness import Harness
from dwinui.ui.screens.prompt import PromptScreen


def run(coro):
    asyncio.run(coro)


def feed(tracker, *lines):
    return [tracker.feed(f"// action:prompt_{line}") for line in lines]


def test_parse():
    tr = PromptTracker()
    changed = feed(tr, "begin Runout", "text Load filament.", "button_group_start",
                   "button Load|LOAD_FILAMENT|primary", "button Unload", "button_group_end",
                   "footer_button Resume|RESUME|warning")
    assert not any(changed) and tr.current is None            # nothing until show
    assert feed(tr, "show") == [True]
    p = tr.current
    assert p.title == "Runout" and p.text == ["Load filament."]
    assert [(b.label, b.gcode, b.color) for b in p.buttons] == [
        ("Load", "LOAD_FILAMENT", "primary"), ("Unload", "Unload", "")]
    assert p.footer[0].gcode == "RESUME"
    assert not tr.feed("// some other response")
    assert feed(tr, "end") == [True] and tr.current is None
    assert feed(tr, "end") == [False]


def test_new_begin_hides_the_old_prompt():
    tr = PromptTracker()
    feed(tr, "begin One", "show")
    assert feed(tr, "begin Two") == [True] and tr.current is None
    feed(tr, "show")
    assert tr.current.title == "Two"


def test_prompt_opens_runs_buttons_and_closes():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.gcode("PROMPT_DEMO")
            await h.settle(0.2)
            screen = h.app.top
            assert isinstance(screen, PromptScreen) and screen.title == "Filament runout"
            await h.click()                                    # first button
            assert "M117 Loading filament" in h.api.gcode_log
            await h.rotate(2)
            await h.click()                                    # Resume: the macro ends the prompt
            await h.settle(0.2)
            assert not isinstance(h.app.top, PromptScreen)
    run(go())


def test_hold_closes_it_in_klipper_too():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.gcode("PROMPT_DEMO")
            await h.settle(0.2)
            await h.back()
            await h.settle(0.1)
            assert not isinstance(h.app.top, PromptScreen)
            assert CLOSE in h.api.gcode_log
    run(go())


def test_prompt_open_before_connecting_is_shown():
    async def go():
        h = Harness(Config(long_press_ms=150, dim_timeout=0))
        h.api.gcode_store += [{"message": "// action:prompt_begin Waiting", "time": 0, "type": "response"},
                              {"message": "// action:prompt_button OK", "time": 0, "type": "response"},
                              {"message": "// action:prompt_show", "time": 0, "type": "response"}]
        async with h:
            await h.settle(0.5)
            assert isinstance(h.app.top, PromptScreen) and h.app.top.title == "Waiting"
    run(go())


def test_prompt_from_before_a_klipper_restart_stays_closed():
    async def go():
        async with Harness(Config(long_press_ms=150, dim_timeout=0)) as h:
            await h.settle(0.3)
            await h.api.gcode("PROMPT_DEMO")
            await h.settle(0.2)
            assert isinstance(h.app.top, PromptScreen)
            await h.api.call("printer.firmware_restart")
            await h.settle(2.5)                               # back to ready, store replayed
            assert h.app.printer.ready
            assert not any(isinstance(s, PromptScreen) for s in h.app.stack)
    run(go())
