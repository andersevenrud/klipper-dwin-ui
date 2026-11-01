"""Pressure advance, machine limits and firmware retraction, as Mainsail's
panels have them.
Changes last until Klipper restarts; the config file stays as it is."""

from __future__ import annotations

from ..screen import Item, ListScreen, NumberEdit


def number_item(screen, label: str, obj: str, field: str, command: str, lo: float, hi: float,
                step: float, fmt: str, unit: str = "") -> Item:
    """A row showing a Klipper value, edited with the knob; `command` gets
    the new value formatted in."""
    get = lambda: float(screen.printer.get(obj, field, 0.0) or 0.0)  # noqa: E731
    shown = fmt + (f" {unit}" if unit else "")
    return Item(label, value=lambda: shown.format(get()),
                edit=NumberEdit(get, lambda v: screen.api.gcode_fire(command.format(fmt.format(v))),
                                lo, hi, step=step, fmt=shown))


class PressureAdvanceScreen(ListScreen):
    title = "Pressure advance"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.set_items([
            number_item(self, "Advance", "extruder", "pressure_advance",
                        "SET_PRESSURE_ADVANCE ADVANCE={}", 0, 2, 0.005, "{:.3f}"),
            number_item(self, "Smooth time", "extruder", "smooth_time",
                        "SET_PRESSURE_ADVANCE SMOOTH_TIME={}", 0, 0.2, 0.005, "{:.3f}", "s"),
        ])


class LimitsScreen(ListScreen):
    title = "Limits"

    def __init__(self, app) -> None:
        super().__init__(app)
        items = [
            number_item(self, "Velocity", "toolhead", "max_velocity", "SET_VELOCITY_LIMIT VELOCITY={}",
                        10, 2000, 10, "{:.0f}", "mm/s"),
            number_item(self, "Acceleration", "toolhead", "max_accel", "SET_VELOCITY_LIMIT ACCEL={}",
                        100, 50000, 100, "{:.0f}"),
            number_item(self, "Corner velocity", "toolhead", "square_corner_velocity",
                        "SET_VELOCITY_LIMIT SQUARE_CORNER_VELOCITY={}", 0, 50, 0.5, "{:.1f}"),
        ]
        toolhead = self.printer.status.get("toolhead", {})
        if toolhead.get("minimum_cruise_ratio") is not None:
            items.append(number_item(self, "Cruise ratio", "toolhead", "minimum_cruise_ratio",
                                     "SET_VELOCITY_LIMIT MINIMUM_CRUISE_RATIO={}", 0, 0.99, 0.05, "{:.2f}"))
        elif toolhead.get("max_accel_to_decel") is not None:          # Klipper before 2024
            items.append(number_item(self, "Accel to decel", "toolhead", "max_accel_to_decel",
                                     "SET_VELOCITY_LIMIT ACCEL_TO_DECEL={}", 100, 50000, 100, "{:.0f}"))
        self.set_items(items)


class RetractionScreen(ListScreen):
    title = "Retraction"

    def __init__(self, app) -> None:
        super().__init__(app)
        obj = "firmware_retraction"
        self.set_items([
            number_item(self, "Retract", obj, "retract_length", "SET_RETRACTION RETRACT_LENGTH={}",
                        0, 10, 0.05, "{:.2f}", "mm"),
            number_item(self, "Retract speed", obj, "retract_speed", "SET_RETRACTION RETRACT_SPEED={}",
                        1, 200, 1, "{:.0f}", "mm/s"),
            number_item(self, "Extra unretract", obj, "unretract_extra_length",
                        "SET_RETRACTION UNRETRACT_EXTRA_LENGTH={}", 0, 5, 0.05, "{:.2f}", "mm"),
            number_item(self, "Unretract speed", obj, "unretract_speed",
                        "SET_RETRACTION UNRETRACT_SPEED={}", 1, 200, 1, "{:.0f}", "mm/s"),
        ])
