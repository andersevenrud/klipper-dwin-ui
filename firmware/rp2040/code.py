# DWIN bridge: Ender 3 V2 display <-> host over USB CDC.
#
# host -> display  frames are forwarded to the display UART unchanged, except
#                  AA EE 10 <ms_hi> <ms_lo> CC 33 C3 3C which beeps locally.
# display -> host  bytes are forwarded as whole frames (split on the tail,
#                  flushed after a short timeout) so injected events never
#                  land mid-frame.
# input            encoder:  AA EE 01 <int8 delta>     CC 33 C3 3C
#                  button:   AA EE 02 <1 press|0 release> CC 33 C3 3C
import time

import board
import busio
import keypad
import pwmio
import rotaryio
import usb_cdc

# --- wiring: adjust to your board -------------------------------------------
PIN_UART_TX = board.GP0     # -> display RX
PIN_UART_RX = board.GP1     # <- display TX
PIN_ENC_A = board.GP2
PIN_ENC_B = board.GP3
PIN_ENC_BTN = board.GP4     # active low
PIN_BEEPER = board.GP5
BEEP_HZ = 2700
ENC_DIVISOR = 4             # detents: the V2 encoder gives 4 edges per click
# ----------------------------------------------------------------------------

TAIL = b"\xcc\x33\xc3\x3c"
FLUSH_NS = 20_000_000       # flush partial display data after 20 ms
MAX_BUF = 1024

uart = busio.UART(PIN_UART_TX, PIN_UART_RX, baudrate=115200, timeout=0,
                  receiver_buffer_size=1024)
host = usb_cdc.data
host.timeout = 0
host.write_timeout = 0.1
encoder = rotaryio.IncrementalEncoder(PIN_ENC_A, PIN_ENC_B, divisor=ENC_DIVISOR)
keys = keypad.Keys((PIN_ENC_BTN,), value_when_pressed=False, pull=True)
beeper = pwmio.PWMOut(PIN_BEEPER, frequency=BEEP_HZ, duty_cycle=0)

host_buf = b""
disp_buf = b""
disp_last = 0
beep_until = 0
last_pos = encoder.position


def send_event(kind, value):
    if host.connected:
        host.write(bytes((0xAA, 0xEE, kind, value & 0xFF)) + TAIL)


def local_command(frame):
    global beep_until
    if len(frame) >= 9 and frame[2] == 0x10:
        ms = (frame[3] << 8) | frame[4]
        beeper.duty_cycle = 0x8000
        beep_until = time.monotonic_ns() + ms * 1_000_000


while True:
    now = time.monotonic_ns()

    # host -> display
    n = host.in_waiting
    if n:
        host_buf += host.read(n)
        while True:
            i = host_buf.find(TAIL)
            if i < 0:
                break
            frame = host_buf[:i + 4]
            host_buf = host_buf[i + 4:]
            if frame[0] == 0xAA and len(frame) > 1 and frame[1] == 0xEE:
                local_command(frame)
            else:
                uart.write(frame)
        if len(host_buf) > MAX_BUF:      # no tail in sight: pass it through
            uart.write(host_buf)
            host_buf = b""

    # display -> host
    n = uart.in_waiting
    if n:
        disp_buf += uart.read(n)
        disp_last = now
    while disp_buf:
        i = disp_buf.find(TAIL)
        if i < 0:
            break
        if host.connected:
            host.write(disp_buf[:i + 4])
        disp_buf = disp_buf[i + 4:]
    if disp_buf and (now - disp_last > FLUSH_NS or len(disp_buf) > MAX_BUF):
        if host.connected:
            host.write(disp_buf)
        disp_buf = b""

    # encoder
    pos = encoder.position
    if pos != last_pos:
        delta = max(-127, min(127, pos - last_pos))
        last_pos += delta
        send_event(0x01, delta)

    # button
    event = keys.events.get()
    if event:
        send_event(0x02, 1 if event.pressed else 0)

    # beeper
    if beep_until and now >= beep_until:
        beeper.duty_cycle = 0
        beep_until = 0
