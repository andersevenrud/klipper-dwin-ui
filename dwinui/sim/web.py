"""Browser viewer for the simulator: shows the panel, sends encoder input.

Keys: arrows / mouse wheel rotate, Enter or Space clicks, Esc or Backspace
long-presses (back).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging

from .link import SimLink

log = logging.getLogger(__name__)

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>DWIN simulator</title>
<style>
body{background:#1b1d22;color:#ccd;font:14px system-ui;display:flex;flex-direction:column;
align-items:center;gap:12px;margin:24px}
img{image-rendering:pixelated;border:12px solid #000;border-radius:8px;width:544px}
button{font:inherit;padding:6px 12px}
</style></head><body>
<img id="lcd" src="/frame.png" alt="display">
<div><button data-ev="left">&#8634; left</button> <button data-ev="click">click</button>
<button data-ev="back">long press</button> <button data-ev="right">right &#8635;</button></div>
<div id="stats"></div>
<script>
const send = ev => fetch('/input/' + ev, {method: 'POST'});
document.querySelectorAll('button').forEach(b => b.onclick = () => send(b.dataset.ev));
const keys = {ArrowLeft:'left', ArrowUp:'left', ArrowRight:'right', ArrowDown:'right',
  Enter:'click', ' ':'click', Escape:'back', Backspace:'back'};
addEventListener('keydown', e => { if (keys[e.key]) { e.preventDefault(); send(keys[e.key]); } });
document.getElementById('lcd').addEventListener('wheel', e => {
  e.preventDefault(); send(e.deltaY > 0 ? 'right' : 'left'); }, {passive: false});
let version = -1;
setInterval(async () => {
  const s = await (await fetch('/state')).json();
  if (s.updates !== version) { version = s.updates;
    document.getElementById('lcd').src = '/frame.png?' + version; }
  document.getElementById('stats').textContent =
    `${s.bytes} bytes sent, ${s.updates} LCD updates, brightness ${s.brightness}`;
}, 150);
</script></body></html>"""


class Viewer:
    def __init__(self, link: SimLink, long_press_ms: int) -> None:
        self.link = link
        self.long_press_ms = long_press_ms

    async def serve(self, host: str, port: int) -> None:
        server = await asyncio.start_server(self._handle, host, port)
        log.info("simulator at http://%s:%d/", host, port)
        print(f"Simulator running: http://{host}:{port}/")
        async with server:
            await server.serve_forever()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            method, path, _ = request.split(b"\r\n", 1)[0].decode().split(" ", 2)
            path = path.split("?", 1)[0]
            status, ctype, body = await self._route(method, path)
            writer.write(f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\n"
                         f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                         "Connection: close\r\n\r\n".encode() + body)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            writer.close()

    async def _route(self, method: str, path: str) -> tuple[str, str, bytes]:
        if path == "/":
            return "200 OK", "text/html", PAGE.encode()
        if path == "/frame.png":
            buf = io.BytesIO()
            self.link.canvas.snapshot(scale=2).save(buf, "PNG")
            return "200 OK", "image/png", buf.getvalue()
        if path == "/state":
            c = self.link.canvas
            body = json.dumps({"updates": c.updates, "bytes": self.link.bytes_written,
                               "brightness": c.brightness})
            return "200 OK", "application/json", body.encode()
        if method == "POST" and path.startswith("/input/"):
            ev = path.rsplit("/", 1)[1]
            if ev == "left":
                self.link.rotate(-1)
            elif ev == "right":
                self.link.rotate(1)
            elif ev == "click":
                self.link.press()
                self.link.release()
            elif ev == "back":
                self.link.press()
                await asyncio.sleep(self.long_press_ms / 1000 + 0.1)
                self.link.release()
            return "204 No Content", "text/plain", b""
        return "404 Not Found", "text/plain", b"not found"
