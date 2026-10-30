"""Custom asset pack lookup (icon ids and font symbol slots).

assets.json is written by tools/build_assets.py next to the DWIN_SET it
describes, so the ids always match what was flashed from the same commit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST = Path(__file__).with_name("assets.json")


@dataclass(frozen=True)
class IconRef:
    lib: int
    id: int
    w: int
    h: int


@dataclass
class Assets:
    icon_lib: int = 0
    icons: dict[str, list[int]] = field(default_factory=dict)
    glyphs: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = MANIFEST) -> Assets:
        data = json.loads(path.read_text())
        return cls(data["icon_lib"], data["icons"], data["glyphs"])

    def icon(self, variant: str, name: str) -> IconRef | None:
        entry = self.icons.get(f"{variant}/{name}")
        return IconRef(self.icon_lib, *entry) if entry else None
