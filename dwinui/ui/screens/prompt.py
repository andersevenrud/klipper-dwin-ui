"""A macro prompt: its text on top and its buttons as a list. The app opens
and closes it as the prompt lines arrive; holding the knob closes it the way
Mainsail's close button does."""

from __future__ import annotations

from ... import protocol as p
from ...prompts import CLOSE, Prompt
from .. import theme as t
from ..screen import Item, ListScreen
from ..widgets import Text, wrap

LINE_H = 18
MAX_LINES = 8
CHARS = (p.WIDTH - 12) // p.FONTS[1][0]


class PromptScreen(ListScreen):
    back_row = False
    ROW_H = 34

    def __init__(self, app, prompt: Prompt) -> None:
        self.title = prompt.title or "Prompt"
        super().__init__(app)
        self.prompt = prompt
        lines = [line for text in prompt.text for line in wrap(text, CHARS, MAX_LINES)][:MAX_LINES]
        y = t.HEADER_H + 8
        for i, line in enumerate(lines):
            self.widgets.append(Text(f"prompt.l{i}", 6, y, CHARS, font=1, value=line))
            y += LINE_H
        self.set_list_top(y + 8 if lines else t.HEADER_H + 4)
        self.set_items([Item(b.label, action=lambda g=b.gcode: self.api.gcode_fire(g))
                        for b in prompt.buttons + prompt.footer])
        self.empty_text = "Hold to close"

    def on_back(self) -> None:
        self.app.close_prompt()
