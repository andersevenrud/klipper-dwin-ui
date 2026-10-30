# Technical notes

Background for anyone changing the code, the bridge firmware or the asset
pack. For setup, see the [README](../README.md).

- [Architecture](#architecture)
- [DWIN protocol](#dwin-protocol)
- [Bridge protocol](#bridge-protocol)
- [Raspberry Pi backend](#raspberry-pi-backend)
- [Display connector](#display-connector)
- [Asset pack](#asset-pack)
- [Development](#development)

## Architecture

```
dwinui/
  protocol.py     frame builders + stream parser (verified against Marlin)
  link.py         Link interface: SerialLink (bridge / USB-UART), PiLink (Pi UART + evdev)
  display.py      handshake, batching, per-widget render cache, invalidation
  moonraker.py    JSON-RPC client, reconnects, Klippy state tracking, sensor discovery
  printer.py      subscribed state model
  history.py      heater temperature history for the graph
  assets.py       asset pack manifest (icon ids, symbol slots)
  config.py       INI loader
  ui/app.py       screen stack, root switching, input/long-press, toasts, dimming, render loop
  ui/widgets.py   Text, Icon, Bar, Tile, ListView (each yields keyed frame parts)
  ui/graph.py     scrolling temperature graph
  ui/screen.py    Screen, ListScreen with inline NumberEdit/Adjust editors, Confirm
  ui/screens/     dashboard, printing, menu, files, temperature, move, extrude, tune, macros, system, blocker
  sim/            renderer, SimLink, FakeMoonraker, browser viewer, scripted harness
firmware/rp2040/  CircuitPython bridge
install/          installer, Pi GPIO setup, udev rule, systemd unit
tools/            asset pack builder, README screenshots
```

**Link budget.** The display runs at 115200 baud, about 11 KB/s, so the UI
never redraws the whole screen to update a value.

**Rendering.**
- **Stable keys:** widgets produce DWIN frames under stable keys, and
  `Display` skips a key when its frames match what is already on the panel.
  A render pass only sends what changed.
- **Fixed-width text:** text fields are padded with background-filled
  cells, so a new value overwrites the old one in a single frame.
- **Screens and overlays:** opening a full screen clears the panel. Dialogs
  and toasts are overlays; closing one blanks its area and redraws only the
  widgets underneath.
- **Expensive drawings:** widgets like the graph yield a `Lazy` part. The
  cache compares its signature, and the frames are only built when the area
  really has to be redrawn.

**The graph** draws in full once. After that, each 5 s sample uses the
panel's area-move command (`0x09`) to shift the plot 1 px left, then draws
only the new right-hand column. That costs about 60 bytes per tick. A test
checks the scrolled result against a full redraw pixel for pixel. The
y-range fits the data in round steps and holds still while the data fills
at least half of it, so full redraws stay rare. History is pre-filled from
Moonraker's `server.temperature_store`.

**Root screen.** The dashboard is the root screen. When a job starts,
wherever it was started from, the printing screen replaces it and the stack
returns to the root. After the job ends, the printing screen stays on
Complete/Cancelled until it's dismissed.

**Backends.** Everything that touches hardware goes through `Link`. A new
backend only has to fill the `frames` and `inputs` queues and implement
`write()`. `SerialLink` uses plain pyserial with a reader thread and a
single writer thread. `serial_asyncio_fast` was dropped because it spins
forever in its write callback on macOS once a write exceeds the OS buffer.

**Moonraker.**
- **Connecting:** the client identifies itself, then polls `printer.info`
  until Klippy is ready.
- **Subscribing:** it asks Klipper's `heaters` object which sensors exist,
  then subscribes to the standard objects plus every sensor.
- **Long gcode:** commands like `G28` hold the RPC open until they finish,
  so they are sent fire-and-forget; errors come back as toasts.

## DWIN protocol

Command bytes were checked against Marlin `src/lcd/dwin/common/dwin_api.cpp`
and `src/lcd/dwin/creality/dwin_lcd.cpp`.

| Cmd | Frame | |
|---|---|---|
| `00` | `AA 00` | handshake. The reply is `AA 00 'O' 'K'` |
| `01` | `AA 01 color` | clear |
| `02` | `AA 02 color w h (x y)…` | points; several coordinates per frame |
| `03` | `AA 03 color x1 y1 x2 y2` | line |
| `05` | `AA 05 mode color x1 y1 x2 y2` | rectangle: 0 frame, 1 fill, 2 XOR |
| `09` | `AA 09 mode<<7\|dir dist color x1 y1 x2 y2` | area move. Mode 1 shifts and fills the gap, mode 0 wraps |
| `11` | `AA 11 flags fg bg x y text` | string. Flags: bit7 width adjust, bit6 draw background, bits 0–3 font |
| `22` | `AA 22 00 id` | show and cache a JPG |
| `23` | `AA 23 x y flags\|lib icon` | icon. `0x80` (IBD) draws the icon's own background |
| `24` | `AA 24 x y flags addr` | draw a JPEG stored in SRAM at `addr` |
| `30` | `AA 30 level` | backlight |
| `31` | `AA 31 5A addr data…` | write up to 128 bytes to the 32 KB SRAM (`A5` = 16 KB flash) |
| `34` | `AA 34 5A A5 dir` | orientation. `1` = portrait 272×480. The panel acknowledges with `AA 34 'O' 'K'` |
| `3D` | `AA 3D` | flush the drawing buffer to the panel |

- **Framing:** all frames end with `CC 33 C3 3C`. Words are big-endian and
  colours RGB565.
- **Fonts:** sizes 0–9 run from 6×12 to 32×64. TJC clone panels only have
  fonts 1–5, so the UI uses those.
- **Characters:** bytes 0x80 and up are read as GB2312 pairs, so there is
  no Latin-1.

**Thumbnails.** The print preview uploads a JPEG with `0x31`, then shows it
with `0x24`. Marlin's ProUI does its G-code previews the same way
(`proui/gcode_preview.cpp`).
- **Source:** dwinui fetches the slicer's PNG over Moonraker's HTTP file
  API. It picks the smallest thumbnail at least 200 px wide.
- **Conversion:** the PNG is composited onto the dialog colour and
  re-encoded as a 200×200 baseline JPEG (4:2:0, quality 85), usually about
  5 KB, or half a second on the link.
- **The tail rule:** the frame tail `CC 33 C3 3C` must not occur inside the
  JPEG data, so on the rare occasion it does, the encoder lowers the
  quality and tries again.

## Bridge protocol

Host→display frames pass through the RP2040 unchanged. The bridge adds
frames with the unused command byte `0xEE`:

| Direction | Frame | Meaning |
|---|---|---|
| bridge → host | `AA EE 01 <int8 delta> CC 33 C3 3C` | encoder detents since last report |
| bridge → host | `AA EE 02 <1/0> CC 33 C3 3C` | button press / release |
| host → bridge | `AA EE 10 <ms_hi> <ms_lo> CC 33 C3 3C` | beep (handled on the bridge, not forwarded) |

- **Whole frames only:** display→host bytes are forwarded as whole frames,
  split on the tail with a 20 ms timeout flush. An injected event never
  lands inside a display reply.
- **USB identity:** `boot.py` names the USB device `dwinui` / `DWIN Bridge`
  and enables a second CDC port. The data port is USB interface 2
  (`…-if02`), which the udev rule links to `/dev/dwin`.

## Raspberry Pi backend

`install/setup-pi-gpio.sh` adds a marked block to `config.txt`:

```ini
[all]
enable_uart=1
dtoverlay=disable-bt
dtoverlay=rotary-encoder,pin_a=17,pin_b=27,relative_axis=1,steps-per-period=1
gpio=17,27=ip,pu
dtoverlay=gpio-key,gpio=22,active_low=1,gpio_pull=up,keycode=256,label=dwin-button
dtoverlay=pwm,pin=18,func=2
```

- **UART:** `disable-bt` gives GPIO14/15 the PL011 UART. Otherwise a Pi 3 or
  4 uses the mini UART there, whose baud rate depends on the CPU clock.
- **Encoder:** `rotary-encoder` in relative mode sends one `REL_X ±1` per
  detent. It has no pull-up parameter, which is why the `gpio=` line is
  there.
- **Button:** `gpio-key` defaults to `KEY_POWER`, and systemd-logind would
  shut the Pi down on a press. `256` is `BTN_0`, which neither logind nor
  the console handles.
- **Device discovery:** `PiLink` finds the input devices by their names
  (`rotary@…`, `button@…`) in `/sys/class/input`.
- **Beeper:** a sysfs PWM channel, optional.

Overlay parameters were checked against the Raspberry Pi kernel's
`arch/arm/boot/dts/overlays/README`. The Pi 5 needs `dtparam=uart0=on` and
does PWM differently, so it isn't supported yet.

## Display connector

The labels are as printed on the display PCB. TX is the display's output.

| Display pin | Signal | Cable mainboard-end hole | Stock mainboard (Creality V4.2.x) |
|---|---|---|---|
| 1 | VCC (5 V) | 10 | 5V |
| 2 | GND | 9 | GND |
| 3 | A (encoder) | 8 | PB15 `BTN_EN1` |
| 4 | B (encoder) | 7 | PB12 `BTN_EN2` |
| 5 | BEEP | 6 | PB13 `BEEPER_PIN` |
| 6 | ENT (button) | 5 | PB14 `BTN_ENC` |
| 7 | TX | 4 | PB11 (USART3 RX) |
| 8 | RX | 3 | PB10 (USART3 TX) |
| 9, 10 | not connected | 2, 1 | — |

- **Sources:** Marlin `pins/stm32f1/pins_BTT_SKR_MINI_E3_common.h` gives the
  display header with its PCB labels, and `pins/stm32f1/pins_CREALITY_V4.h`
  gives the mainboard EXP3 header.
- **Cable reversal:** the two sources agree once you account for the stock
  cable reversing the numbering (mainboard pin *n* connects to display pin
  *11 − n*).
- **Levels:** the signals measured 3.2 V on a real display, so they are safe
  for 3.3 V GPIO.

## Asset pack

`tools/build_assets.py` writes `build/DWIN_SET`, `build/preview` and
`dwinui/assets.json`. The JSON tells the UI the icon IDs and symbol slots,
so commit it along with any asset change. Sources are pinned and
checksummed in `.cache/`.

**Publishing.** `.github/workflows/assets.yml` rebuilds the pack when
`tools/build_assets.py`, the theme, the protocol or `assets.json` change on
`main`, or when started by hand.
- **Release:** it replaces `DWIN_SET.zip` on the rolling `assets` release,
  so the README link stays the same.
- **Zip contents:** besides `DWIN_SET/`, the zip carries the Terminus (OFL)
  and Tabler (MIT) license texts, which both licenses require on
  redistribution.
- **Manifest check:** the build fails if the committed `dwinui/assets.json`
  doesn't match the pack it just built.

**Memory layout.** Images live in sixteen 32 KB banks. The file's leading
number is its first bank, and that same number is the image ID in `0x22`
and the library ID in `0x23`.

| Bank | File | |
|---|---|---|
| font memory | `0T5UIC1.HZK` | Terminus Bold, all 10 sizes, plus symbols in control-code slots |
| 0 | `0_start.jpg` | boot splash |
| 1–2 | stock language JPGs | untouched |
| 3–4 | `3.ICO` | Tabler icons, pre-rendered on BG, TILE_BG and ACCENT |
| 9–15 | stock `9.ICO` | untouched, so stock firmware keeps working |

The icons are baked onto the theme colours. If `BG`, `TILE_BG` or `ACCENT`
in `ui/theme.py` changes, rebuild and reflash the pack.

**Formats.**
- **Font (`.HZK`):** ten 1-bit tables of 128 glyphs, one per size, in size
  order. Rows run top to bottom, padded to whole bytes, with the most
  significant bit as the leftmost pixel; total 125,440 bytes.
- **Symbols:** `° ← → ↑ ↓ … • ± × ▲ ▼ ▶ ◀ ✓ µ` live in slots 0x01–0x07 and
  0x0E–0x15. With the stock font they fall back to single ASCII characters,
  so fixed-width fields keep their length.
- **Icons (`.ICO`):** a 4 KB directory of 256 × 16-byte big-endian entries
  (width, height, offset, 24-bit length), followed by baseline JPEGs.
- **Splash:** a landscape 480×272 JPEG with the portrait content rotated
  90° counter-clockwise. It must stay well under 32 KB; a 50 KB splash is
  known to break the display.

**Flashing details.**
- **Card:** MBR, FAT32 with 4 KB clusters, and only `DWIN_SET` at the root.
  Cards formatted in Finder have failed for people. On macOS,
  `diskutil partitionDisk diskN 2 MBR "MS-DOS FAT32" DWIN 4G "Free Space" REST 0`
  gives one 4 GB partition with 4 KB clusters.
- **Partial sets:** fine. Only the banks a file covers get overwritten.
- **Panel firmware:** don't include the display's `.BIN` or `.CFG`.
- **Recovery:** flash the
  [stock set](https://github.com/mriscoc/Ender3V2S1/tree/Ender3V2S1-Bugfix/display%20assets/stock).
- **Clones:** TJC panels use a different format (`TJC_SET`) and can't take
  this pack.

## Development

```sh
uv venv && uv pip install -e '.[dev]'
.venv/bin/python -m pytest                                    # tests
.venv/bin/dwinui --sim --fake                                 # simulator, fake printer: http://127.0.0.1:8080/
.venv/bin/dwinui --sim --url ws://printer.local:7125/websocket   # simulator, real printer
.venv/bin/dwinui --fake --display-port /dev/cu.usbmodemXXXX     # real display, fake printer
.venv/bin/python -m dwinui.sim.harness shots/                 # screenshot tour of every screen
.venv/bin/python tools/build_assets.py                        # asset pack
.venv/bin/python tools/readme_screens.py --preview ws://printer.local:7125/websocket 3DBenchy.gcode
                                                              # docs/img/print-preview.png (read-only)
.venv/bin/python tools/readme_screens.py                      # docs/img/screens.png, uses the preview above
```

- **Simulator:** it decodes the exact byte stream the UI sends and renders
  it with Pillow.
  - **Controls:** in the browser, the arrow keys and mouse wheel turn the
    encoder, Enter clicks, and Esc long-presses (back).
  - **Byte counter:** the page shows the bytes sent, to keep an eye on the
    link budget.
  - **Real assets:** add `--custom-assets` to draw text and icons from the
    built `.HZK` and `.ICO`, so the preview matches the panel pixel for
    pixel.
- **Fake printer:** `FakeMoonraker` simulates heaters, prints, homing and
  errors, and mirrors a real printer's sensor list.
