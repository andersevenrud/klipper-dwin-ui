"""Colours (RGB565) and layout constants."""

from ..protocol import HEIGHT, WIDTH, rgb

BG = rgb(14, 16, 20)
HEADER_BG = rgb(30, 38, 50)
FG = rgb(235, 238, 242)
DIM = rgb(130, 140, 152)
ACCENT = rgb(0, 132, 230)
EDIT = rgb(240, 150, 0)
TILE_BG = rgb(30, 36, 46)
DISABLED = rgb(80, 86, 96)
OK = rgb(40, 180, 90)
WARN = rgb(235, 150, 20)
ERROR = rgb(200, 40, 40)
HOT = rgb(255, 110, 60)
BAR_BG = rgb(40, 46, 56)

HEADER_H = 36
TOAST_RECT = (0, HEIGHT - 48, WIDTH - 1, HEIGHT - 1)

__all__ = [n for n in dir() if n.isupper()]
