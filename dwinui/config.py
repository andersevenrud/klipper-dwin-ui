"""INI configuration."""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Preset:
    name: str
    extruder: float
    heater_bed: float


@dataclass
class Config:
    # [display]
    port: str = "/dev/dwin"
    baud: int = 115200
    backend: str = "bridge"            # bridge | serial
    brightness: int = 255
    dim_brightness: int = 24
    dim_timeout: float = 120.0         # seconds, 0 disables
    long_press_ms: int = 600
    invert_encoder: bool = False
    beep_on_click: bool = False
    beep_ms: int = 20
    # [moonraker]
    url: str = "ws://localhost:7125/websocket"
    api_key: str | None = None
    # [ui]
    max_extruder_temp: float = 300.0
    max_bed_temp: float = 120.0
    min_extrude_temp: float = 170.0
    xy_feedrate: float = 3000.0        # mm/min
    z_feedrate: float = 600.0
    extrude_feedrate: float = 300.0
    babystep: float = 0.01
    confirm_estop: bool = True
    confirm_macros: bool = True
    show_hidden_macros: bool = False
    presets: list[Preset] = field(default_factory=lambda: [
        Preset("PLA", 200, 60), Preset("PETG", 235, 80), Preset("ABS", 250, 100),
    ])


def load(path: str | Path | None) -> Config:
    cfg = Config()
    if path is None:
        return cfg
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    if not parser.read(path):
        raise FileNotFoundError(path)

    def section(name: str, fields: dict[str, type]) -> None:
        if not parser.has_section(name):
            return
        sec = parser[name]
        for key, typ in fields.items():
            if key not in sec:
                continue
            if typ is bool:
                value = sec.getboolean(key)
            elif typ is int:
                value = sec.getint(key)
            elif typ is float:
                value = sec.getfloat(key)
            else:
                value = sec.get(key)
            setattr(cfg, key, value)

    section("display", {
        "port": str, "baud": int, "backend": str, "brightness": int,
        "dim_brightness": int, "dim_timeout": float, "long_press_ms": int,
        "invert_encoder": bool, "beep_on_click": bool, "beep_ms": int,
    })
    section("moonraker", {"url": str, "api_key": str})
    section("ui", {
        "max_extruder_temp": float, "max_bed_temp": float, "min_extrude_temp": float,
        "xy_feedrate": float, "z_feedrate": float, "extrude_feedrate": float,
        "babystep": float, "confirm_estop": bool, "confirm_macros": bool,
        "show_hidden_macros": bool,
    })

    presets = [
        Preset(name.split(None, 1)[1],
               parser[name].getfloat("extruder", 0.0),
               parser[name].getfloat("heater_bed", 0.0))
        for name in parser.sections() if name.startswith("preset ")
    ]
    if presets:
        cfg.presets = presets
    if cfg.backend not in ("bridge", "serial"):
        raise ValueError(f"unknown display backend {cfg.backend!r}")
    return cfg
