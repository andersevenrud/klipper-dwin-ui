"""DWIN T5UIC1 serial protocol: frame builders and a stream parser.

Frame layout: 0xAA <cmd> <payload...> CC 33 C3 3C. Multi-byte values are
big-endian, colours are RGB565. Layouts verified against Marlin
src/lcd/dwin/common/dwin_api.cpp and src/lcd/dwin/creality/dwin_lcd.cpp.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER = 0xAA
TAIL = b"\xcc\x33\xc3\x3c"

WIDTH = 272
HEIGHT = 480

# Font id -> (char width, char height). Only 1-5 exist on TJC clone panels,
# so the UI sticks to those.
FONTS = {
    0: (6, 12),
    1: (8, 16),
    2: (10, 20),
    3: (12, 24),
    4: (14, 28),
    5: (16, 32),
    6: (20, 40),
    7: (24, 48),
    8: (28, 56),
    9: (32, 64),
}

RECT_FRAME = 0
RECT_FILL = 1
RECT_XOR = 2

# Bridge extension: an unused command byte carries input events and beeps.
BRIDGE_CMD = 0xEE
EV_ROTATE = 0x01
EV_BUTTON = 0x02
BRIDGE_BEEP = 0x10

MAX_FRAME = 512


def rgb(r: int, g: int, b: int) -> int:
    """Convert 8-bit RGB to RGB565."""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def rgb565_to_rgb(c: int) -> tuple[int, int, int]:
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)


def frame(payload: bytes) -> bytes:
    return bytes((HEADER,)) + payload + TAIL


def _clamp_x(x: int) -> int:
    return max(0, min(WIDTH - 1, x))


def _clamp_y(y: int) -> int:
    return max(0, min(HEIGHT - 1, y))


def handshake() -> bytes:
    return frame(b"\x00")


def set_dir(direction: int = 1) -> bytes:
    """0=0deg, 1=90deg (portrait 272x480, what Marlin uses), 2=180, 3=270."""
    return frame(bytes((0x34, 0x5A, 0xA5, direction)))


def update() -> bytes:
    """Push the drawing buffer to the panel; send after each batch."""
    return frame(b"\x3d")


def brightness(level: int) -> bytes:
    return frame(bytes((0x30, max(0, min(255, level)))))


def clear(color: int) -> bytes:
    return frame(struct.pack(">BH", 0x01, color))


def line(color: int, x1: int, y1: int, x2: int, y2: int) -> bytes:
    return frame(struct.pack(">BHHHHH", 0x03, color,
                             _clamp_x(x1), _clamp_y(y1), _clamp_x(x2), _clamp_y(y2)))


def rect(mode: int, color: int, x1: int, y1: int, x2: int, y2: int) -> bytes:
    """Rectangle with inclusive corners. mode: 0 frame, 1 fill, 2 XOR fill."""
    return frame(struct.pack(">BBHHHHH", 0x05, mode, color,
                             _clamp_x(x1), _clamp_y(y1), _clamp_x(x2), _clamp_y(y2)))


def fill(color: int, x1: int, y1: int, x2: int, y2: int) -> bytes:
    return rect(RECT_FILL, color, x1, y1, x2, y2)


POINTS_PER_FRAME = 20     # Marlin keeps point frames to about this size


def points(color: int, coords: list[tuple[int, int]], size: int = 1) -> bytes:
    """Draw size x size dots; several coordinates share one 0x02 frame."""
    out = b""
    for i in range(0, len(coords), POINTS_PER_FRAME):
        xy = b"".join(struct.pack(">HH", _clamp_x(x), _clamp_y(y))
                      for x, y in coords[i:i + POINTS_PER_FRAME])
        out += frame(struct.pack(">BHBB", 0x02, color, size, size) + xy)
    return out


MOVE_LEFT, MOVE_RIGHT, MOVE_UP, MOVE_DOWN = 0, 1, 2, 3


def area_move(direction: int, distance: int, color: int, x1: int, y1: int, x2: int, y2: int,
              wrap: bool = False) -> bytes:
    """Shift a screen area (the panel does the work); the gap fills with color."""
    mode = 0 if wrap else 1
    return frame(struct.pack(">BBHHHHHH", 0x09, (mode << 7) | direction, distance, color,
                             _clamp_x(x1), _clamp_y(y1), _clamp_x(x2), _clamp_y(y2)))


# Symbols the UI uses. A custom font (tools/build_assets.py) stores them in
# control-code slots; with the stock font they fall back to one ASCII char,
# so fixed-width fields keep their length either way.
FALLBACK = {
    "°": " ", "←": "<", "→": ">", "↑": "^", "↓": "v", "…": "~", "•": "*", "±": "~",
    "×": "x", "▲": "^", "▼": "v", "▶": ">", "◀": "<", "✓": "*", "µ": "u",
}
_glyphs: dict[str, int] = {}


def set_glyphs(mapping: dict[str, int]) -> None:
    """Use the custom font's symbol slots (char -> byte)."""
    _glyphs.clear()
    _glyphs.update(mapping)


def encode_text(s: str) -> bytes:
    out = bytearray()
    for ch in s:
        if ch in _glyphs:
            out.append(_glyphs[ch])
            continue
        ch = FALLBACK.get(ch, ch)
        code = ord(ch)
        out.append(code if 0x20 <= code < 0x7F else 0x3F)
    return bytes(out)


def text(x: int, y: int, s: str, font: int = 2, fg: int = 0xFFFF, bg: int = 0x0000,
         show_bg: bool = True) -> bytes:
    """Draw a string. show_bg paints each character cell with bg.

    The string is clipped to what fits between x and the right edge.
    """
    cw, _ = FONTS[font]
    room = max(0, (WIDTH - x) // cw)
    data = encode_text(s)[:room]
    flags = (0x40 if show_bg else 0) | (font & 0x0F)
    return frame(struct.pack(">BBHHHH", 0x11, flags, fg, bg, _clamp_x(x), _clamp_y(y)) + data)


def jpg(image_id: int) -> bytes:
    return frame(bytes((0x22, 0x00, image_id)))


def icon(lib: int, icon_id: int, x: int, y: int, flags: int = 0x80) -> bytes:
    """Show icon_id from the .ICO library in bank lib. flags 0x80 = IBD (draw the baked background)."""
    return frame(struct.pack(">BHHBB", 0x23, _clamp_x(x), _clamp_y(y), flags | (lib & 0x1F), icon_id))


def bridge_beep(ms: int) -> bytes:
    ms = max(0, min(0xFFFF, ms))
    return frame(bytes((BRIDGE_CMD, BRIDGE_BEEP, ms >> 8, ms & 0xFF)))


def is_handshake_reply(payload: bytes) -> bool:
    return payload[:3] == b"\x00OK"


@dataclass(frozen=True)
class Rotate:
    delta: int


@dataclass(frozen=True)
class Button:
    pressed: bool


InputEvent = Rotate | Button


def parse_input(payload: bytes) -> InputEvent | None:
    if len(payload) < 3 or payload[0] != BRIDGE_CMD:
        return None
    kind, value = payload[1], payload[2]
    if kind == EV_ROTATE:
        return Rotate(value - 256 if value > 127 else value)
    if kind == EV_BUTTON:
        return Button(bool(value))
    return None


class FrameParser:
    """Split a byte stream into frame payloads (without header and tail)."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        out: list[bytes] = []
        while True:
            start = self._buf.find(HEADER)
            if start < 0:
                self._buf.clear()
                break
            if start:
                del self._buf[:start]
            end = self._buf.find(TAIL, 1)
            if end < 0:
                if len(self._buf) > MAX_FRAME:
                    del self._buf[:1]
                    continue
                break
            out.append(bytes(self._buf[1:end]))
            del self._buf[:end + len(TAIL)]
        return out
