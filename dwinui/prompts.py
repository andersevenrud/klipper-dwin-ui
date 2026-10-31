"""Macro prompts: the dialogs macros open with RESPOND TYPE=command
MSG="action:prompt_...", as Mainsail and KlipperScreen show them.

    // action:prompt_begin Filament runout
    // action:prompt_text Load new filament, then resume.
    // action:prompt_button Resume|RESUME|primary
    // action:prompt_footer_button Cancel|CANCEL_PRINT|error
    // action:prompt_show
    // action:prompt_end

A button is "label|gcode|color"; the gcode defaults to the label. Button
groups (prompt_button_group_start/end) only arrange buttons side by side,
so their buttons are kept in order and the grouping is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PREFIX = "// action:prompt_"
CLOSE = 'RESPOND TYPE=command MSG="action:prompt_end"'


@dataclass
class PromptButton:
    label: str
    gcode: str
    color: str = ""

    @classmethod
    def parse(cls, message: str) -> PromptButton:
        label, gcode, color = (message.split("|") + ["", ""])[:3]
        label = label.strip()
        return cls(label, gcode.strip() or label, color.strip())


@dataclass
class Prompt:
    title: str
    text: list[str] = field(default_factory=list)
    buttons: list[PromptButton] = field(default_factory=list)
    footer: list[PromptButton] = field(default_factory=list)


class PromptTracker:
    """Follows the prompt lines in Klipper's responses. `current` is the
    prompt on show, or None."""

    def __init__(self) -> None:
        self.current: Prompt | None = None
        self._building: Prompt | None = None

    def reset(self) -> None:
        self.current = self._building = None

    def feed(self, line: str) -> bool:
        """One response line; True when the prompt on show changed."""
        line = line.strip()
        if not line.startswith(PREFIX):
            return False
        kind, _, message = line[len(PREFIX):].partition(" ")
        message = message.strip()
        if kind == "begin":
            changed = self.current is not None
            self.current, self._building = None, Prompt(message)
            return changed
        if kind == "end":
            changed = self.current is not None
            self.reset()
            return changed
        prompt = self._building
        if prompt is None:
            return False
        if kind == "text":
            prompt.text.append(message)
        elif kind == "button":
            prompt.buttons.append(PromptButton.parse(message))
        elif kind == "footer_button":
            prompt.footer.append(PromptButton.parse(message))
        elif kind == "show":
            self.current = Prompt(prompt.title, list(prompt.text), list(prompt.buttons),
                                  list(prompt.footer))
            return True
        return False
