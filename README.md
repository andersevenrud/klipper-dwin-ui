# klipper-dwin-ui

A KlipperScreen-like UI for the stock **Ender 3 V2 DWIN display** (T5UIC1,
272×480, rotary encoder) running against Klipper through Moonraker.

The display runs DWIN's own firmware and acts as a dumb serial graphics
terminal. All UI logic runs on the Klipper host. An RP2040 bridge links the
display to the host over USB and also handles the encoder, button and beeper.

```
 Ender 3 V2 display ──UART/GPIO── RP2040 (CircuitPython) ──USB── Pi: dwinui ──ws── Moonraker ── Klipper
```

## Screens

| Screen | What it does |
|---|---|
| Status (root) | State, file, hotend and bed temperatures, speed/flow/fan, Z, Z offset, filament, progress, elapsed, remaining and finish time |
| Menu | 2×4 tile grid. During a print it shows Pause/Resume and Cancel |
| Files | Newest first. Clicking a file shows slicer metadata, then starts the print |
| Temperature | Inline target editors, preheat presets from the config, cooldown |
| Move | Home all/XY/Z, step size, encoder jog (each detent is one step), disable motors |
| Extrude | Length and speed, extrude/retract, cold-extrude guard |
| Tune | Speed (M220), flow (M221), fan (M106), babystep Z (`SET_GCODE_OFFSET Z_ADJUST MOVE=1`), heater targets |
| Macros | `gcode_macro` objects. Names starting with `_` are hidden by default |
| System | E-stop, firmware restart, Klipper restart, host reboot/shutdown, backlight |
| Blocker | Takes over while Moonraker or Klippy isn't ready: shows the state message and restart actions |

**Input:** rotate to move the selection or change a value, click to select,
and hold (600 ms) to go back or cancel an edit. Error responses (`!! ...`)
and failed requests show as red toasts with a beep. After two minutes idle
the backlight dims, and the next input only wakes the display.

## Hardware

### Display connector pinout

The labels are as printed on the display PCB. TX is the display's output
and RX its input. The stock cable reverses the numbering, so mainboard-end
pin *n* connects to display pin *11 − n*.

| Display pin | Signal | Cable mainboard-end pin | Stock mainboard (Creality V4.2.x) |
|---|---|---|---|
| 1 | VCC (5 V) | 10 | 5V |
| 2 | GND | 9 | GND |
| 3 | A (encoder) | 8 | PB15 `BTN_EN1` |
| 4 | B (encoder) | 7 | PB12 `BTN_EN2` |
| 5 | BEEP | 6 | PB13 `BEEPER_PIN` |
| 6 | ENT (button) | 5 | PB14 `BTN_ENC` |
| 7 | TX (display → host) | 4 | PB11 (USART3 RX) |
| 8 | RX (host → display) | 3 | PB10 (USART3 TX) |
| 9, 10 | not connected | 2, 1 | — |

Sources: Marlin `pins/stm32f1/pins_BTT_SKR_MINI_E3_common.h` (display
header, PCB labels) and `pins/stm32f1/pins_CREALITY_V4.h` (mainboard EXP3).
The two agree once the cable reversal is accounted for. Before inserting
duponts, beep each hole out against the labelled display pins; that avoids
any confusion about connector orientation.

### Verify before connecting signals

- [ ] **Logic levels.** The display UART is expected to be 3.3 V. Confirm the
      encoder/button pull-ups go to 3.3 V, not 5 V, before connecting them
      to the RP2040.
- [ ] **Beeper drive.** Check whether the beeper line drives a transistor on
      the display board or the buzzer directly. An RP2040 pin supplies only
      a few mA.
- [ ] **Encoder direction.** If it's backwards, set `invert_encoder: true`.

### Bridge wiring (defaults in `firmware/rp2040/code.py`)

| RP2040 | Display |
|---|---|
| VBUS (5 V), physical pin 40 | 1 VCC |
| GND, physical pin 3 or 38 | 2 GND |
| GP0 (UART0 TX), physical pin 1 | 8 RX |
| GP1 (UART0 RX), physical pin 2 | 7 TX |
| GP2, physical pin 4 | 3 A |
| GP3, physical pin 5 | 4 B |
| GP4, physical pin 6 | 6 ENT (active low, internal pull-up) |
| GP5, physical pin 7, through a ~1 kΩ resistor | 5 BEEP (PWM, 2.7 kHz) |

### Flashing the bridge

1. Install CircuitPython 9.x for your board. `boot.py` needs 8.0 or newer.
2. Copy `firmware/rp2040/boot.py` and `code.py` to `CIRCUITPY`.
3. Power-cycle the board so `boot.py` runs. The board then shows up as
   `/dev/serial/by-id/usb-dwinui_DWIN_Bridge_<serial>-if02` (the data port).
   `if00` is the REPL console.

Bridge protocol: host→display frames pass through unchanged. The bridge
adds frames with the unused command byte `0xEE`:

| Direction | Frame | Meaning |
|---|---|---|
| bridge → host | `AA EE 01 <int8 delta> CC 33 C3 3C` | encoder detents since last report |
| bridge → host | `AA EE 02 <1/0> CC 33 C3 3C` | button press / release |
| host → bridge | `AA EE 10 <ms_hi> <ms_lo> CC 33 C3 3C` | beep (handled on the bridge, not forwarded) |

Display→host bytes are forwarded as whole frames, so an injected event never
lands inside a display reply.

## Install on the Klipper host

```sh
cd ~ && git clone <this repo> klipper-dwin-ui
~/klipper-dwin-ui/install/install.sh
```

The installer sets up:

- the `~/dwinui-env` virtualenv,
- `~/printer_data/config/dwinui.conf`, copied from
  `config/dwinui.example.conf` only if it doesn't exist yet,
- a udev rule (`/dev/dwin`),
- the `dwinui.service` systemd unit,
- a `dwinui` line in `moonraker.asvc`, so Mainsail/Fluidd can restart the
  service.

Optional Moonraker updates (`moonraker.conf`):

```ini
[update_manager dwinui]
type: git_repo
path: ~/klipper-dwin-ui
origin: <this repo's URL>
primary_branch: main
virtualenv: ~/dwinui-env
requirements: requirements.txt
managed_services: dwinui
```

Logs: `journalctl -u dwinui -f`.

## Simulator

The simulator needs no hardware. It decodes the exact byte stream the UI
would send and renders it with Pillow.

```sh
uv venv && uv pip install -e '.[dev]'
.venv/bin/dwinui --sim --fake                     # fake printer, http://127.0.0.1:8080/
.venv/bin/dwinui --sim --url ws://klipper-ender.local:7125/websocket   # real printer
.venv/bin/python -m dwinui.sim.harness shots/     # screenshot tour of every screen
.venv/bin/python -m pytest
```

In the browser, the arrow keys and mouse wheel turn the encoder, Enter
clicks, and Esc long-presses (back). The page also shows how many bytes have
been sent, to keep an eye on the ~11 KB/s link budget.

## Layout

```
dwinui/
  protocol.py     frame builders + stream parser (verified against Marlin)
  link.py         Link interface; SerialLink (bridge or plain USB-UART)
  display.py      handshake, batching, per-widget render cache, invalidation
  moonraker.py    JSON-RPC client, reconnects, Klippy state tracking
  printer.py      subscribed state model
  config.py       INI loader
  ui/app.py       screen stack, input/long-press, toasts, dimming, render loop
  ui/widgets.py   Text, Bar, Tile, ListView (each yields keyed frame parts)
  ui/screen.py    Screen, ListScreen with inline NumberEdit/Adjust editors, Confirm
  ui/screens/     status, menu, files, temperature, move, extrude, tune, macros, system, blocker
  sim/            renderer, SimLink, FakeMoonraker, browser viewer, scripted harness
firmware/rp2040/  CircuitPython bridge
install/          udev rule, systemd unit, installer
```

**Rendering.** Widgets produce DWIN frames under stable keys. The display
skips a key when its frames match what is already on the panel, so a render
pass only sends what changed. Text fields are fixed-width and padded with
background-filled cells, so a new value overwrites the old one in a single
frame. Opening a full screen clears the panel. Dialogs and toasts are
overlays: closing one blanks its area and redraws only the widgets
underneath.

**Adding a Pi GPIO backend.** Subclass `Link`. Read display replies from the
UART into `frames`, and push `Rotate`/`Button` events from evdev (for
example, the `rotary-encoder` overlay) into `inputs`.

## Protocol notes

Command bytes were checked against Marlin `src/lcd/dwin/common/dwin_api.cpp`
and `src/lcd/dwin/creality/dwin_lcd.cpp`:

| Cmd | Frame | |
|---|---|---|
| `00` | `AA 00` | handshake. The reply is `AA 00 'O' 'K'` |
| `01` | `AA 01 color` | clear |
| `03` | `AA 03 color x1 y1 x2 y2` | line |
| `05` | `AA 05 mode color x1 y1 x2 y2` | rectangle: 0 frame, 1 fill, 2 XOR |
| `11` | `AA 11 flags fg bg x y text` | string. Flags: bit7 width adjust, bit6 draw background, bits 0–3 font |
| `22` | `AA 22 00 id` | show and cache a JPG |
| `23` | `AA 23 x y flags\|lib icon` | icon. Creality's UI uses flags `0x80` |
| `30` | `AA 30 level` | backlight |
| `34` | `AA 34 5A A5 dir` | orientation. `1` = portrait 272×480 |
| `3D` | `AA 3D` | flush the drawing buffer to the panel |

All frames end with `CC 33 C3 3C`. Words are big-endian and colours RGB565.
Fonts 0–9 run from 6×12 to 32×64, but **TJC clone panels only have fonts
1–5**, so the UI uses those. Icons from the stock `9.ICL` are supported by
`protocol.icon()` but not used yet; the UI draws only rectangles and text.
