"""Decode DWIN frames and render them to a PIL image."""

from __future__ import annotations

import logging
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .. import protocol as p

log = logging.getLogger(__name__)

FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "C:/Windows/Fonts/consola.ttf",
)


def _load_font(height: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    size = max(8, int(height * 0.8))
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


class Canvas:
    """Panel model: a drawing buffer that becomes visible on 0x3D update."""

    def __init__(self) -> None:
        self.buffer = Image.new("RGB", (p.WIDTH, p.HEIGHT), (0, 0, 0))
        self.screen = self.buffer.copy()
        self._draw = ImageDraw.Draw(self.buffer)
        self._fonts: dict[int, object] = {}
        self._parser = p.FrameParser()
        self.brightness = 255
        self.frames_seen = 0
        self.updates = 0
        self.unknown: list[int] = []

    def feed(self, data: bytes) -> None:
        for payload in self._parser.feed(data):
            self.apply(payload)

    def font(self, size: int):
        if size not in self._fonts:
            self._fonts[size] = _load_font(p.FONTS[size][1])
        return self._fonts[size]

    def apply(self, payload: bytes) -> None:
        if not payload:
            return
        self.frames_seen += 1
        cmd, args = payload[0], payload[1:]
        d = self._draw
        color = p.rgb565_to_rgb
        if cmd == 0x00 or cmd == 0x34:
            pass
        elif cmd == 0x01:
            (c,) = struct.unpack(">H", args[:2])
            d.rectangle((0, 0, p.WIDTH, p.HEIGHT), fill=color(c))
        elif cmd == 0x03:
            c, x1, y1, x2, y2 = struct.unpack(">HHHHH", args[:10])
            d.line((x1, y1, x2, y2), fill=color(c))
        elif cmd == 0x05:
            mode, c, x1, y1, x2, y2 = struct.unpack(">BHHHHH", args[:11])
            box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            if mode == p.RECT_FRAME:
                d.rectangle(box, outline=color(c))
            elif mode == p.RECT_FILL:
                d.rectangle(box, fill=color(c))
            else:
                region = self.buffer.crop((box[0], box[1], box[2] + 1, box[3] + 1))
                key = bytes(color(c)) * (region.width * region.height)
                data = bytes(a ^ b for a, b in zip(region.tobytes(), key))
                self.buffer.paste(Image.frombytes("RGB", region.size, data), box[:2])
        elif cmd == 0x11:
            flags, fg, bg, x, y = struct.unpack(">BHHHH", args[:9])
            text = args[9:].decode("ascii", "replace")
            size = flags & 0x0F
            if size not in p.FONTS:
                self.unknown.append(cmd)
                return
            cw, ch = p.FONTS[size]
            font = self.font(size)
            if flags & 0x40:
                d.rectangle((x, y, x + cw * len(text) - 1, y + ch - 1), fill=color(bg))
            for i, char in enumerate(text):
                if char != " ":
                    d.text((x + i * cw + cw / 2, y + ch / 2), char, fill=color(fg), font=font, anchor="mm")
        elif cmd == 0x30:
            self.brightness = args[0]
        elif cmd == 0x3D:
            self.updates += 1
            self.screen = self.buffer.copy()
        elif cmd in (0x22, 0x23):
            # No ICL/JPG assets in the simulator: draw a placeholder.
            if cmd == 0x23:
                x, y = struct.unpack(">HH", args[:4])
                d.rectangle((x, y, x + 15, y + 15), outline=(255, 0, 255))
        else:
            self.unknown.append(cmd)

    def snapshot(self, scale: int = 1) -> Image.Image:
        img = self.screen
        if self.brightness < 255:
            img = Image.eval(img, lambda v: v * max(self.brightness, 10) // 255)
        if scale != 1:
            img = img.resize((p.WIDTH * scale, p.HEIGHT * scale), Image.NEAREST)
        return img
