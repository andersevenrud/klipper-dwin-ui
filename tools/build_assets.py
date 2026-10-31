#!/usr/bin/env python3
"""Build the custom DWIN_SET asset pack: font, icon library and boot splash.

    python tools/build_assets.py [--weight n|b]

Writes build/DWIN_SET/ (copy that folder to the SD card root), the
third-party license texts in build/LICENSES/, previews in build/preview/, and
dwinui/assets.json, which tells the UI the icon ids and glyph slots. Sources
are downloaded once into .cache/ and verified by SHA-256.

Display memory layout (32 KB banks, 16 of them):
  bank 0      0_start.jpg   boot splash (replaces Creality's)
  banks 1-2   stock language JPGs, untouched
  banks 3-8   3.ICO         our icons (must stay below bank 9)
  banks 9-15  stock 9.ICO, untouched, so stock firmware still works
The font lives in its own memory as 0T5UIC1.HZK.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import tarfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dwinui import protocol as p  # noqa: E402
from dwinui.ui import theme as t  # noqa: E402

CACHE = ROOT / ".cache"
OUT = ROOT / "build"
MANIFEST = ROOT / "dwinui" / "assets.json"

SOURCES = {
    "terminus.tar.gz": (
        "https://downloads.sourceforge.net/project/terminus-font/terminus-font-4.49/terminus-font-4.49.1.tar.gz",
        "d961c1b781627bf417f9b340693d64fc219e0113ad3a3af1a3424c7aa373ef79"),
    "tabler-icons.ttf": (
        "https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.49.0/dist/fonts/tabler-icons.ttf",
        "d291676dc4c28b386552753bf16cf2bef0e93e5543f8e0b5c5d72c0da41ae1bc"),
    "tabler-icons.css": (
        "https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.49.0/dist/tabler-icons.css",
        "a0562b340ab94e3aca60a144f6b95a056f792744f2bde38a5e5352a2aa0918d0"),
    "tabler-LICENSE": (
        "https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.49.0/LICENSE",
        "b740a1d46122672da62833e97f7e7c8a13fa85cbc7445b584b297cc00dde93db"),
    "klipper-logo.png": (
        "https://raw.githubusercontent.com/Klipper3d/klipper/57cf0759c0b3096daeda3d39e1184a374bfe2b0b/docs/img/klipper-logo.png",
        "9213fcef709d03d91d33b055d63b37defe0af13a9516e77c001c43f034a53728"),
}

NOTICE = """\
Asset pack for klipper-dwin-ui (https://github.com/andersevenrud/klipper-dwin-ui)

Copy only the DWIN_SET folder to the root of a microSD card with a single
FAT32 partition of 8 GB or less (4 KB clusters, MBR; the card itself can be
bigger). Insert it into the display while it is off,
then power up: blue while flashing, orange when done. dwinui uses the
pack by default (custom_assets: true in dwinui.conf).

Contents:
  0T5UIC1.HZK  font: Terminus Font by Dimitar Zhekov, converted to the
               display's bitmap format (SIL Open Font License 1.1, see
               LICENSES/Terminus-OFL.txt)
  3.ICO        icons: Tabler Icons (MIT, see LICENSES/Tabler-LICENSE.txt)
  0_start.jpg  boot splash: the Klipper logo, from
               https://github.com/Klipper3d/klipper (GPLv3)
"""

ICON_LIB = 3
ICON_LIB_MAX_BANK = 8
BANK = 32 * 1024
SPLASH_MAX = 30 * 1024    # bank 0 is 32 KB; a 50 KB splash is known to break the display

# DWIN font id -> cell size, Terminus pixel size, integer scale
FONT_SLOTS = [
    (6, 12, 12, 1), (8, 16, 16, 1), (10, 20, 20, 1), (12, 24, 24, 1), (14, 28, 28, 1),
    (16, 32, 32, 1), (20, 40, 20, 2), (24, 48, 24, 2), (28, 56, 28, 2), (32, 64, 32, 2),
]

# Unicode characters stored in unused control-code slots of the ASCII table.
# 0x09, 0x0A and 0x0D are skipped in case the panel treats them as whitespace.
GLYPH_SLOTS = {
    0x01: "°", 0x02: "←", 0x03: "→", 0x04: "↑", 0x05: "↓", 0x06: "…", 0x07: "•",
    0x0E: "±", 0x0F: "×", 0x10: "▲", 0x11: "▼", 0x12: "▶", 0x13: "◀", 0x14: "✓", 0x15: "µ",
    0x16: "↻", 0x17: "³",
}

# UI name -> Tabler icon name
TILE_ICONS = {
    "files": "folder", "temp": "flame", "move": "arrows-move", "extrude": "arrow-bar-to-down",
    "tune": "adjustments", "macros": "terminal-2", "system": "settings", "back": "arrow-left",
    "pause": "player-pause", "resume": "player-play", "cancel": "player-stop",
}
STATUS_ICONS = {
    "hotend": "flame", "bed": "ripple", "speed": "gauge", "flow": "droplet", "fan": "propeller",
    "z": "arrows-vertical", "offset": "arrow-autofit-height", "filament": "spiral",
    "elapsed": "clock", "remaining": "hourglass",
}
# variant -> (pixel size, foreground, background, icon set)
VARIANTS = {
    "tile": (40, t.FG, t.TILE_BG, TILE_ICONS),
    "tile_sel": (40, t.FG, t.ACCENT, TILE_ICONS),
    "small": (20, t.FG, t.BG, STATUS_ICONS),
}
# Icons added after the first release. They go at the end of the library so
# every existing icon id stays the same, and a display flashed with an older
# pack still shows the older icons correctly.
LATER_TILE_ICONS = {"fans": "propeller", "lights": "bulb", "print": "printer",
                    "calibrate": "ruler-measure"}


def fetch(name: str) -> Path:
    url, digest = SOURCES[name]
    path = CACHE / name
    if not path.exists():
        CACHE.mkdir(exist_ok=True)
        print(f"downloading {url}")
        with urllib.request.urlopen(url) as resp:
            path.write_bytes(resp.read())
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != digest:
        path.unlink()
        raise SystemExit(f"{name}: checksum mismatch ({actual})")
    return path


def rgb(color565: int) -> tuple[int, int, int]:
    return p.rgb565_to_rgb(color565)


# -- font -----------------------------------------------------------------------

def read_bdf(text: str) -> tuple[int, dict[int, tuple[tuple[int, int, int, int], list[int], int]]]:
    """Return (descent, {codepoint: ((w, h, xoff, yoff), rows, row_bits)})."""
    descent = int(re.search(r"^FONT_DESCENT (\d+)", text, re.M).group(1))
    glyphs = {}
    for block in text.split("STARTCHAR")[1:]:
        enc = int(re.search(r"^ENCODING (-?\d+)", block, re.M).group(1))
        bbx = tuple(int(v) for v in re.search(r"^BBX (.+)$", block, re.M).group(1).split())
        hexrows = block.split("BITMAP", 1)[1].split("ENDCHAR", 1)[0].split()
        glyphs[enc] = (bbx, [int(r, 16) for r in hexrows], len(hexrows[0]) * 4 if hexrows else 0)
    return descent, glyphs


def glyph_cell(glyph, descent: int, w: int, h: int) -> list[list[bool]]:
    cell = [[False] * w for _ in range(h)]
    if glyph is None:
        return cell
    (gw, gh, xoff, yoff), rows, bits = glyph
    top = (h - descent) - (yoff + gh)
    for gy, row in enumerate(rows):
        for gx in range(gw):
            if row >> (bits - 1 - gx) & 1:
                x, y = xoff + gx, top + gy
                if 0 <= x < w and 0 <= y < h:
                    cell[y][x] = True
    return cell


def build_font(terminus: Path, weight: str) -> tuple[bytes, list[Image.Image]]:
    tar = tarfile.open(terminus)
    bdfs = {}
    for size in {s for _, _, s, _ in FONT_SLOTS}:
        member = next(m for m in tar.getmembers() if m.name.endswith(f"/ter-u{size}{weight}.bdf"))
        bdfs[size] = read_bdf(tar.extractfile(member).read().decode("latin-1"))

    data = bytearray()
    previews = []
    for slot, (w, h, size, scale) in enumerate(FONT_SLOTS):
        descent, glyphs = bdfs[size]
        sw, sh = w // scale, h // scale
        preview = Image.new("1", (16 * w, 8 * h))
        for code in range(128):
            source = ord(GLYPH_SLOTS[code]) if code in GLYPH_SLOTS else code
            if code < 0x20 and code not in GLYPH_SLOTS or code == 0x7F:
                source = -1
            cell = glyph_cell(glyphs.get(source), descent, sw, sh)
            stride = (w + 7) // 8
            for y in range(h):
                row = cell[y // scale]
                value = 0
                for x in range(w):
                    if row[x // scale]:
                        value |= 1 << (stride * 8 - 1 - x)
                        preview.putpixel(((code % 16) * w + x, (code // 16) * h + y), 1)
                data += value.to_bytes(stride, "big")
        previews.append(preview)
        missing = [c for c in GLYPH_SLOTS.values() if ord(c) not in glyphs]
        if missing:
            print(f"  font {slot}: Terminus lacks {''.join(missing)}")
    assert len(data) == 125_440, len(data)
    return bytes(data), previews


# -- icons -------------------------------------------------------------------------

def tabler_codepoints(css: Path) -> dict[str, int]:
    return {m[0]: int(m[1], 16) for m in
            re.findall(r'\.ti-([a-z0-9-]+):before\s*\{\s*content:\s*"\\([0-9a-f]+)"', css.read_text())}


def render_icon(font_path: Path, codepoint: int, size: int, fg: int, bg: int) -> Image.Image:
    img = Image.new("RGB", (size, size), rgb(bg))
    font = ImageFont.truetype(str(font_path), size)
    ImageDraw.Draw(img).text((size / 2, size / 2), chr(codepoint), fill=rgb(fg), font=font, anchor="mm")
    return img


def jpeg(img: Image.Image, quality: int = 95, subsampling: int = 0) -> bytes:
    import io
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, subsampling=subsampling, progressive=False)
    return buf.getvalue()


def pack_ico(entries: list[tuple[int, int, bytes]]) -> bytes:
    """DWIN .ICO: 256 x 16-byte big-endian directory, then the JPEGs."""
    if len(entries) > 256:
        raise ValueError("an ICO holds at most 256 icons")
    header = bytearray()
    body = bytearray()
    offset = 4096
    for w, h, blob in entries:
        n = len(blob)
        header += struct.pack(">HHLBHBBBBB", w, h, offset, n >> 16, n & 0xFFFF, 0, 0, 0, 0, 0)
        body += blob
        offset += n
    header += bytes(4096 - len(header))
    return bytes(header) + bytes(body)


def build_icons(ttf: Path, css: Path) -> tuple[bytes, dict, Image.Image]:
    codepoints = tabler_codepoints(css)
    entries = []
    index: dict[str, list[int]] = {}
    tiles = []
    order = [(variant, name, tabler) for variant, (_, _, _, names) in VARIANTS.items()
             for name, tabler in names.items()]
    order += [(variant, name, tabler) for name, tabler in LATER_TILE_ICONS.items()
              for variant in ("tile", "tile_sel")]
    for variant, name, tabler in order:
        size, fg, bg, _ = VARIANTS[variant]
        if tabler not in codepoints:
            raise SystemExit(f"Tabler has no icon named {tabler!r}")
        img = render_icon(ttf, codepoints[tabler], size, fg, bg)
        index[f"{variant}/{name}"] = [len(entries), size, size]
        entries.append((size, size, jpeg(img)))
        tiles.append(img)
    ico = pack_ico(entries)
    sheet = Image.new("RGB", (12 * 44, (len(tiles) + 11) // 12 * 44), rgb(t.BG))
    for i, img in enumerate(tiles):
        sheet.paste(img, ((i % 12) * 44 + 2, (i // 12) * 44 + 2))
    return ico, index, sheet


# -- splash ------------------------------------------------------------------------

def build_splash(logo_path: Path) -> tuple[bytes, Image.Image]:
    logo = Image.open(logo_path).convert("RGBA")
    # Lighten the slate parts so they read on a dark background; keep the red.
    px = logo.load()
    light = rgb(t.FG)
    for y in range(logo.height):
        for x in range(logo.width):
            r, g, b, a = px[x, y]
            if a and not (r > g + 60):
                px[x, y] = (*light, a)
    target_w = 200
    logo = logo.resize((target_w, round(logo.height * target_w / logo.width)), Image.LANCZOS)
    portrait = Image.new("RGB", (p.WIDTH, p.HEIGHT), rgb(t.BG))
    portrait.paste(logo, ((p.WIDTH - logo.width) // 2, (p.HEIGHT - logo.height) // 2 - 20), logo)
    # Panel JPGs are stored landscape with the portrait content turned 90 degrees CCW.
    landscape = portrait.rotate(90, expand=True)
    for quality in (92, 85, 75):
        blob = jpeg(landscape, quality, subsampling=2)
        if len(blob) <= SPLASH_MAX:
            return blob, portrait
    raise SystemExit("splash does not fit in one bank")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weight", choices="nb", default="b", help="Terminus normal or bold")
    args = ap.parse_args()

    dwin_set = OUT / "DWIN_SET"
    preview = OUT / "preview"
    dwin_set.mkdir(parents=True, exist_ok=True)
    preview.mkdir(parents=True, exist_ok=True)

    hzk, font_previews = build_font(fetch("terminus.tar.gz"), args.weight)
    (dwin_set / "0T5UIC1.HZK").write_bytes(hzk)
    for slot, img in enumerate(font_previews):
        img.save(preview / f"font{slot}.png")

    ico, index, sheet = build_icons(fetch("tabler-icons.ttf"), fetch("tabler-icons.css"))
    last_bank = ICON_LIB + (len(ico) - 1) // BANK
    if last_bank > ICON_LIB_MAX_BANK:
        raise SystemExit(f"{ICON_LIB}.ICO is {len(ico)} bytes and would reach bank {last_bank}")
    (dwin_set / f"{ICON_LIB}.ICO").write_bytes(ico)
    sheet.save(preview / "icons.png")

    splash, portrait = build_splash(fetch("klipper-logo.png"))
    (dwin_set / "0_start.jpg").write_bytes(splash)
    portrait.save(preview / "splash.png")

    licenses = OUT / "LICENSES"
    licenses.mkdir(exist_ok=True)
    with tarfile.open(fetch("terminus.tar.gz")) as tar:
        ofl = next(m for m in tar.getmembers() if m.name.endswith("/OFL.TXT"))
        (licenses / "Terminus-OFL.txt").write_bytes(tar.extractfile(ofl).read())
    (licenses / "Tabler-LICENSE.txt").write_bytes(fetch("tabler-LICENSE").read_bytes())
    (OUT / "README.txt").write_text(NOTICE)

    MANIFEST.write_text(json.dumps({
        "icon_lib": ICON_LIB,
        "icons": index,
        "glyphs": {ch: code for code, ch in GLYPH_SLOTS.items()},
    }, indent=1, ensure_ascii=False) + "\n")

    print(f"0T5UIC1.HZK  {len(hzk):7d} bytes  (Terminus {'bold' if args.weight == 'b' else 'normal'})")
    print(f"{ICON_LIB}.ICO        {len(ico):7d} bytes  {len(index)} icons, banks {ICON_LIB}-{last_bank}")
    print(f"0_start.jpg  {len(splash):7d} bytes  bank 0")
    print(f"-> {dwin_set}\n-> {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
