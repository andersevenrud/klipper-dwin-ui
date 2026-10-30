"""Colours (RGB565) and layout constants.

Klipper/Mainsail look: Klipper's logo red as the accent, Vuetify dark-theme
neutrals (what Mainsail uses), and Mainsail's temperature chart colours.
Icons are baked onto BG, TILE_BG and ACCENT, so changing those three means
rebuilding and reflashing the asset pack (tools/build_assets.py).
"""

from ..protocol import HEIGHT, WIDTH, rgb

BG = rgb(0x12, 0x12, 0x12)
HEADER_BG = rgb(0x27, 0x27, 0x27)
TILE_BG = rgb(0x1E, 0x1E, 0x1E)
FG = rgb(0xEE, 0xEE, 0xEE)
DIM = rgb(0x9E, 0x9E, 0x9E)
DISABLED = rgb(0x5A, 0x5A, 0x5A)
ACCENT = rgb(0xB1, 0x2F, 0x35)       # Klipper logo red
EDIT = rgb(0xFB, 0x8C, 0x00)         # editing a value (Vuetify warning orange)
OK = rgb(0x4C, 0xAF, 0x50)
WARN = rgb(0xFB, 0x8C, 0x00)
ERROR = rgb(0xFF, 0x52, 0x52)
BAR_BG = rgb(0x2D, 0x2D, 0x2D)

# Mainsail temperature chart colours
HOT = rgb(0xF4, 0x43, 0x36)          # extruder
HOT_DIM = rgb(0x7A, 0x22, 0x1B)
BED = rgb(0x21, 0x96, 0xF3)          # heater_bed
BED_DIM = rgb(0x10, 0x4B, 0x79)
CHAMBER = rgb(0x4C, 0xAF, 0x50)
GRAPH_BG = rgb(0x18, 0x18, 0x18)
GRID = rgb(0x33, 0x33, 0x33)

HEADER_H = 36
TOAST_RECT = (0, HEIGHT - 48, WIDTH - 1, HEIGHT - 1)

__all__ = [n for n in dir() if n.isupper()]
