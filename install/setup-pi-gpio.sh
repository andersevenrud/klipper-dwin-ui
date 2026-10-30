#!/usr/bin/env bash
# Prepare a Raspberry Pi (3, 4, Zero 2) to drive the DWIN display straight from
# its GPIO header (display backend "pi"):
#   - PL011 UART on GPIO14/15 (Bluetooth off), no login console on it
#   - encoder via the kernel rotary-encoder overlay, button via gpio-key
#     (BTN_0, so neither systemd-logind nor the console react to it)
#   - beeper on hardware PWM0
#   - the user joins the dialout, input and gpio groups
# The config.txt block sits between markers; re-running replaces it.
# Override pins with ENC_A=, ENC_B=, BUTTON=, BEEPER= (BCM numbers). Reboot afterwards.
set -euo pipefail

ENC_A=${ENC_A:-17}
ENC_B=${ENC_B:-27}
BUTTON=${BUTTON:-22}
BEEPER=${BEEPER:-18}

CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt
MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)

case "$MODEL" in
    *"Raspberry Pi 5"*)
        echo "Pi 5 is not supported by this script yet: its GPIO14/15 UART needs"
        echo "dtparam=uart0=on (/dev/ttyAMA0) and its PWM works differently." >&2
        exit 1 ;;
    *"Raspberry Pi"*) ;;
    *) echo "This does not look like a Raspberry Pi ($MODEL)." >&2; exit 1 ;;
esac

echo "==> $MODEL, $CONFIG"
sudo cp "$CONFIG" "$CONFIG.dwinui-backup"
sudo sed -i '/^# >>> dwinui$/,/^# <<< dwinui$/d' "$CONFIG"
sudo tee -a "$CONFIG" >/dev/null <<BLOCK
# >>> dwinui
[all]
enable_uart=1
dtoverlay=disable-bt
dtoverlay=rotary-encoder,pin_a=$ENC_A,pin_b=$ENC_B,relative_axis=1,steps-per-period=1
gpio=$ENC_A,$ENC_B=ip,pu
dtoverlay=gpio-key,gpio=$BUTTON,active_low=1,gpio_pull=up,keycode=256,label=dwin-button
dtoverlay=pwm,pin=$BEEPER,func=2
# <<< dwinui
BLOCK

echo "==> serial console off, UART on"
if command -v raspi-config >/dev/null; then
    sudo raspi-config nonint do_serial_cons 1
    sudo raspi-config nonint do_serial_hw 0
else
    echo "raspi-config not found: remove console=serial0,... from cmdline.txt yourself" >&2
fi
sudo systemctl disable --now hciuart 2>/dev/null || true

echo "==> groups for $USER"
sudo usermod -aG dialout,input,gpio "$USER"

cat <<DONE

Done (backup: $CONFIG.dwinui-backup). Now:
  1. sudo reboot
  2. in dwinui.conf, [display]:
         backend: pi
         beeper_pwm: /sys/class/pwm/pwmchip0
  3. sudo systemctl restart dwinui
DONE
