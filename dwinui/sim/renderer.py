"""Decode DWIN frames and render them to a PIL image.

With an asset directory (a built DWIN_SET), text is drawn from the real
0T5UIC1.HZK bitmaps and icons from the real .ICO files, so screenshots match
the panel pixel for pixel. Without one, a system monospace font stands in.
"""

from __future__ import annotations

import io
import logging
import re
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


def load_hzk(path: Path) -> dict[int, tuple[int, bytes]]:
    """font id -> (offset, data) over the ASCII tables of a T5UIC1 font file."""
    data = path.read_bytes()
    tables, offset = {}, 0
    for font, (w, h) in sorted(p.FONTS.items()):
        size = 128 * h * ((w + 7) // 8)
        tables[font] = (offset, data)
        offset += size
    return tables


def load_ico(path: Path) -> list[tuple[int, int, bytes]]:
    data = path.read_bytes()
    icons = []
    for i in range(256):
        w, h, off, l3, l21 = struct.unpack(">HHLBH", data[i * 16:i * 16 + 11])
        length = (l3 << 16) | l21
        if not length:
            break
        icons.append((w, h, data[off:off + length]))
    return icons


class Canvas:
    """Panel model: a drawing buffer that becomes visible on 0x3D update."""

    def __init__(self, assets: Path | None = None) -> None:
        self.buffer = Image.new("RGB", (p.WIDTH, p.HEIGHT), (0, 0, 0))
        self.screen = self.buffer.copy()
        self._draw = ImageDraw.Draw(self.buffer)
        self._fonts: dict[int, object] = {}
        self._parser = p.FrameParser()
        self.brightness = 255
        self.frames_seen = 0
        self.updates = 0
        self.unknown: list[int] = []
        self.hzk: dict[int, tuple[int, bytes]] | None = None
        self.icons: dict[int, list[tuple[int, int, bytes]]] = {}
        self.jpgs: dict[int, Path] = {}
        if assets:
            self.load_assets(assets)

    def load_assets(self, folder: Path) -> None:
        for path in folder.iterdir():
            m = re.match(r"(\d+)", path.name)
            if path.name.upper() == "0T5UIC1.HZK":
                self.hzk = load_hzk(path)
            elif m and path.suffix.upper() == ".ICO":
                self.icons[int(m.group(1))] = load_ico(path)
            elif m and path.suffix.lower() == ".jpg":
                self.jpgs[int(m.group(1))] = path

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
        elif cmd == 0x02:
            c, w, h = struct.unpack(">HBB", args[:4])
            for i in range(4, len(args) - 3, 4):
                x, y = struct.unpack(">HH", args[i:i + 4])
                d.rectangle((x, y, x + w - 1, y + h - 1), fill=color(c))
        elif cmd == 0x09:
            mode_dir, dist, c, x1, y1, x2, y2 = struct.unpack(">BHHHHHH", args[:13])
            self._area_move(mode_dir >> 7, mode_dir & 0x0F, dist, color(c), x1, y1, x2, y2)
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
            if flags & 0x40:
                d.rectangle((x, y, x + cw * len(text) - 1, y + ch - 1), fill=color(bg))
            if self.hzk:
                self._hzk_text(x, y, args[9:], size, color(fg))
                return
            font = self.font(size)
            reverse = {v: k for k, v in p._glyphs.items()}
            for i, code in enumerate(args[9:]):
                char = reverse.get(code, chr(code))
                if char != " ":
                    d.text((x + i * cw + cw / 2, y + ch / 2), char, fill=color(fg), font=font, anchor="mm")
        elif cmd == 0x30:
            self.brightness = args[0]
        elif cmd == 0x3D:
            self.updates += 1
            self.screen = self.buffer.copy()
        elif cmd == 0x22:
            path = self.jpgs.get(args[1])
            if path:
                # Stored landscape, turned 90 degrees CCW; undo that for portrait.
                self.buffer.paste(Image.open(path).convert("RGB").rotate(-90, expand=True), (0, 0))
        elif cmd == 0x23:
            x, y, flags, icon_id = struct.unpack(">HHBB", args[:6])
            library = self.icons.get(flags & 0x0F, [])
            if icon_id < len(library):
                _, _, blob = library[icon_id]
                img = Image.open(io.BytesIO(blob)).convert("RGB")
                if flags & 0x80:
                    self.buffer.paste(img, (x, y))
                else:
                    # IBD=0: black is transparent (BFI widens it to near-black).
                    limit = 24 if flags & 0x20 else 4
                    mask = img.convert("L").point(lambda v: 255 if v > limit else 0)
                    self.buffer.paste(img, (x, y), mask)
            else:
                d.rectangle((x, y, x + 15, y + 15), outline=(255, 0, 255))
        else:
            self.unknown.append(cmd)

    def _area_move(self, mode: int, direction: int, dist: int, fill: tuple[int, int, int],
                   x1: int, y1: int, x2: int, y2: int) -> None:
        """0x09: mode 1 shifts and fills the gap, mode 0 wraps around."""
        box = (x1, y1, x2 + 1, y2 + 1)
        region = self.buffer.crop(box)
        w, h = region.size
        dx, dy = {0: (-dist, 0), 1: (dist, 0), 2: (0, -dist), 3: (0, dist)}[direction]
        moved = Image.new("RGB", region.size, fill)
        moved.paste(region, (dx, dy))
        if mode == 0:   # wrap: paste the part that fell off onto the other side
            moved.paste(region, (dx + (w if dx < 0 else -w if dx > 0 else 0),
                                 dy + (h if dy < 0 else -h if dy > 0 else 0)))
        self.buffer.paste(moved, box[:2])

    def _hzk_text(self, x: int, y: int, data: bytes, size: int, fg: tuple[int, int, int]) -> None:
        offset, font = self.hzk[size]
        w, h = p.FONTS[size]
        stride = (w + 7) // 8
        px = self.buffer.load()
        for i, code in enumerate(data):
            if code >= 0x80:
                continue          # GB2312 is not modelled
            base = offset + code * h * stride
            for row in range(h):
                bits = int.from_bytes(font[base + row * stride:base + (row + 1) * stride], "big")
                if not bits:
                    continue
                for col in range(w):
                    if bits >> (stride * 8 - 1 - col) & 1:
                        X, Y = x + i * w + col, y + row
                        if X < p.WIDTH and Y < p.HEIGHT:
                            px[X, Y] = fg

    def snapshot(self, scale: int = 1) -> Image.Image:
        img = self.screen
        if self.brightness < 255:
            img = Image.eval(img, lambda v: v * max(self.brightness, 10) // 255)
        if scale != 1:
            img = img.resize((p.WIDTH * scale, p.HEIGHT * scale), Image.NEAREST)
        return img
