"""Entry point: python -m dwinui -c dwinui.conf [--sim] [--fake]"""

from __future__ import annotations

import argparse
import asyncio
import logging

from . import config
from .link import Link, LinkClosed, SerialLink
from .moonraker import Moonraker
from .printer import Printer
from .ui.app import App

log = logging.getLogger("dwinui")


async def display_loop(app: App, link: Link) -> None:
    while True:
        try:
            await link.open()
            await app.run()
        except (LinkClosed, TimeoutError) as e:
            log.warning("display link: %s", e)
        finally:
            await link.close()
        await asyncio.sleep(2)


async def amain(args: argparse.Namespace) -> None:
    cfg = config.load(args.config)
    if args.url:
        cfg.url = args.url
    printer = Printer()
    if args.fake:
        from .sim.fake_moonraker import FakeMoonraker
        api = FakeMoonraker(printer)
    else:
        api = Moonraker(printer, cfg.url, cfg.api_key)

    tasks = [api.run()]
    if args.sim:
        from .sim.link import SimLink
        from .sim.web import Viewer
        link: Link = SimLink()
        tasks.append(Viewer(link, cfg.long_press_ms).serve(args.host, args.port))
    else:
        link = SerialLink(cfg.port, cfg.baud, bridge=cfg.backend == "bridge")

    app = App(cfg, link, api, printer)
    tasks.append(display_loop(app, link))
    await asyncio.gather(*tasks)


def main() -> None:
    ap = argparse.ArgumentParser(prog="dwinui", description="Klipper UI for the Ender 3 V2 DWIN display")
    ap.add_argument("-c", "--config", help="path to dwinui.conf")
    ap.add_argument("--sim", action="store_true", help="render to a browser simulator instead of serial")
    ap.add_argument("--fake", action="store_true", help="use a fake printer instead of Moonraker")
    ap.add_argument("--url", help="Moonraker websocket URL (overrides config)")
    ap.add_argument("--host", default="127.0.0.1", help="simulator listen address")
    ap.add_argument("--port", type=int, default=8080, help="simulator port")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
