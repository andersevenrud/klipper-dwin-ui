"""Cancel: the whole print, or one object of it (Klipper's exclude_object).

Choosing a row is the confirmation, like the button of the dialog it
replaces. The list starts on Back, so a double press on the Cancel tile
doesn't cancel anything.
"""

from __future__ import annotations

import re
from collections import Counter

from ..screen import Item, ListScreen

# OrcaSlicer, PrusaSlicer and SuperSlicer label objects "<name>_id_<n>_copy_<n>".
SLICER_NAME = re.compile(r"^(?P<base>.+?)(?:\.(?:stl|3mf|obj|step|stp))?_id_\d+_copy_(?P<copy>\d+)$",
                         re.IGNORECASE)


def object_labels(names: list[str]) -> dict[str, str]:
    """Readable labels: 'SHAPE-BOX_ID_0_COPY_1' -> 'Shape-box copy 1'.
    Klipper upper-cases the names. Labels that would end up the same keep
    the name as it is."""
    labels = {}
    for name in names:
        m = SLICER_NAME.match(name)
        if m:
            copy = int(m.group("copy"))
            label = m.group("base").replace("_", " ") + (f" copy {copy}" if copy else "")
        else:
            label = name.replace("_", " ")
        label = label.lower()
        labels[name] = label[:1].upper() + label[1:]
    counts = Counter(labels.values())
    return {name: label if counts[label] == 1 else name for name, label in labels.items()}


def print_objects(printer) -> list[str]:
    return [o.get("name", "") for o in printer.get("exclude_object", "objects", []) or [] if o.get("name")]


class CancelScreen(ListScreen):
    title = "Cancel"

    def __init__(self, app) -> None:
        super().__init__(app)
        names = print_objects(self.printer)
        labels = object_labels(names)
        items = [Item("Whole print", action=self._cancel_print)]
        for name in names:
            items.append(Item(labels[name], value=lambda n=name: self._state(n),
                              action=lambda n=name: self._exclude(n, labels[n]),
                              enabled=lambda n=name: n not in self._excluded()))
        self.set_items(items)
        self.list.selected = 0                  # Back

    def _excluded(self) -> list[str]:
        return self.printer.get("exclude_object", "excluded_objects", []) or []

    def _state(self, name: str) -> str:
        if name in self._excluded():
            return "excluded"
        return "printing" if self.printer.get("exclude_object", "current_object") == name else ""

    def _cancel_print(self) -> None:
        self.api.fire("printer.print.cancel")
        self.app.pop_to_root()

    def _exclude(self, name: str, label: str) -> None:
        quoted = f'"{name}"' if " " in name else name
        self.api.gcode_fire(f"EXCLUDE_OBJECT NAME={quoted}")
        self.app.toast(f"Excluded {label}")
