"""PiLink against stand-ins: FIFOs carrying real input_event records for the
encoder/button devices, a pty for the UART and a fake sysfs tree."""

import asyncio
import os
import struct
import sys
import tty

import pytest

from dwinui import protocol as p
from dwinui.config import load
from dwinui.link import EV_KEY, EV_REL, EVENT_FMT, PiLink, PwmBeeper, find_input

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs FIFOs and a pty")


def event(etype: int, code: int, value: int) -> bytes:
    return struct.pack(EVENT_FMT, 0, 0, etype, code, value)


def test_find_input_by_overlay_name(tmp_path):
    for i, name in enumerate(["vc4-hdmi", "rotary@11", "button@16"]):
        (tmp_path / f"event{i}" / "device").mkdir(parents=True)
        (tmp_path / f"event{i}" / "device" / "name").write_text(name + "\n")
    assert find_input("auto", "rotary", tmp_path) == "/dev/input/event1"
    assert find_input("auto", "button", tmp_path) == "/dev/input/event2"
    assert find_input("auto", "nothing", tmp_path) is None
    assert find_input(str(tmp_path / "event*"), "x") == str(tmp_path / "event0")


def test_pi_link_events_and_uart(tmp_path):
    async def run():
        enc, btn = tmp_path / "enc", tmp_path / "btn"
        os.mkfifo(enc)
        os.mkfifo(btn)
        master, slave = os.openpty()
        tty.setraw(slave)
        link = PiLink(os.ttyname(slave), encoder=str(enc), button=str(btn))
        await link.open()
        enc_w = os.open(enc, os.O_WRONLY)
        btn_w = os.open(btn, os.O_WRONLY)
        try:
            # Three detents one way, one back, delivered in one read; then a click.
            os.write(enc_w, event(EV_REL, 0, 1) + event(EV_REL, 0, 1) + event(EV_REL, 0, 1))
            got = await asyncio.wait_for(link.inputs.get(), 1)
            assert got == p.Rotate(3)
            os.write(btn_w, event(EV_KEY, 256, 1) + event(EV_KEY, 256, 2) + event(EV_KEY, 256, 0))
            assert await asyncio.wait_for(link.inputs.get(), 1) == p.Button(True)
            assert await asyncio.wait_for(link.inputs.get(), 1) == p.Button(False)  # autorepeat skipped
            # The UART side is a plain serial link (no bridge frames, no beeps).
            await link.write(p.handshake())
            await asyncio.sleep(0.05)
            assert os.read(master, 64) == p.handshake()
            await link.beep(10)                                  # no beeper configured: no-op
        finally:
            await link.close()
            for fd in (enc_w, btn_w, master, slave):
                os.close(fd)
    asyncio.run(run())


def test_pwm_beeper_sysfs(tmp_path):
    async def run():
        chip = tmp_path / "pwmchip0"
        (chip / "pwm0").mkdir(parents=True)
        for name in ("enable", "period", "duty_cycle"):
            (chip / "pwm0" / name).write_text("0")
        b = PwmBeeper(str(chip), 0, hz=2500)
        await b.setup()
        assert (chip / "pwm0" / "period").read_text() == "400000"
        assert (chip / "pwm0" / "duty_cycle").read_text() == "200000"
        b.on()
        assert (chip / "pwm0" / "enable").read_text() == "1"
        b.off()
        assert (chip / "pwm0" / "enable").read_text() == "0"
    asyncio.run(run())


def test_pi_backend_config(tmp_path):
    conf = tmp_path / "dwinui.conf"
    conf.write_text("[display]\nbackend: pi\nbeeper_pwm: /sys/class/pwm/pwmchip0\n")
    cfg = load(conf)
    assert cfg.backend == "pi" and cfg.port == "/dev/serial0"
    assert cfg.beeper_pwm == "/sys/class/pwm/pwmchip0"
    conf.write_text("[display]\nbackend: pi\nport: /dev/ttyAMA0\n")
    assert load(conf).port == "/dev/ttyAMA0"
