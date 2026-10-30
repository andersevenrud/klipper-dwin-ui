"""Slicer thumbnails, converted for the panel.

The T5UIC1 can only decode baseline JPEGs, so the PNG Moonraker serves is
composited onto the dialog colour, fitted into a square and re-encoded.
"""

from __future__ import annotations

import io
import posixpath

from . import protocol as p


def pick(metadata: dict, size: int) -> str | None:
    """Relative path (from the gcodes root) of the best thumbnail: the
    smallest one at least `size` wide, else the largest there is."""
    thumbs = [t for t in metadata.get("thumbnails") or [] if t.get("relative_path")]
    if not thumbs:
        return None
    big = [t for t in thumbs if t.get("width", 0) >= size]
    best = min(big, key=lambda t: t["width"]) if big else max(thumbs, key=lambda t: t.get("width", 0))
    folder = posixpath.dirname(metadata.get("filename", ""))
    return posixpath.join(folder, best["relative_path"])


def to_jpeg(png: bytes, size: int, background: tuple[int, int, int]) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGBA")
    img.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (*background, 255))
    canvas.alpha_composite(img, ((size - img.width) // 2, (size - img.height) // 2))
    rgb = canvas.convert("RGB")
    for quality in range(85, 50, -5):
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=quality, subsampling=2, progressive=False)
        data = buf.getvalue()
        # The frame tail must not occur inside the data, or the panel would
        # cut the upload short. Vanishingly rare; re-encoding changes the bytes.
        if p.TAIL not in data:
            return data
    raise ValueError("could not encode thumbnail")
