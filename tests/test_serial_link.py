import asyncio
import os
import sys
import tty

import pytest

from dwinui import protocol as p
from dwinui.display import Display
from dwinui.link import SerialLink

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")


def test_serial_link_over_pty():
    async def run():
        master, slave = os.openpty()
        tty.setraw(slave)
        link = SerialLink(os.ttyname(slave), bridge=True)
        await link.open()
        loop = asyncio.get_running_loop()
        received = bytearray()

        def on_bridge_rx():
            data = os.read(master, 4096)
            received.extend(data)
            if p.handshake() in data:
                # Reply split across writes, with an input event right behind it.
                os.write(master, b"\xaa\x00O")
                os.write(master, b"K" + p.TAIL + bytes.fromhex("aaee01fd") + p.TAIL)

        loop.add_reader(master, on_bridge_rx)
        try:
            display = Display(link)
            await asyncio.wait_for(display.handshake(), 3)
            ev = await asyncio.wait_for(link.inputs.get(), 1)
            assert ev == p.Rotate(-3)
            await link.beep(50)
            await asyncio.sleep(0.1)
            assert p.bridge_beep(50) in received
        finally:
            loop.remove_reader(master)
            await link.close()
            os.close(master)
            os.close(slave)
    asyncio.run(run())
