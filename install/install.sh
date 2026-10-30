#!/usr/bin/env bash
# Install dwinui on the Klipper host: venv, config, udev rule, systemd unit,
# and Moonraker service permission. Safe to re-run.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${VENV:-$HOME/dwinui-env}"
PRINTER_DATA="${PRINTER_DATA:-$HOME/printer_data}"
CONFIG="$PRINTER_DATA/config/dwinui.conf"

echo "==> virtualenv at $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$REPO/requirements.txt"
"$VENV/bin/pip" install -q -e "$REPO" --no-deps

if [ ! -f "$CONFIG" ]; then
    echo "==> config at $CONFIG"
    cp "$REPO/config/dwinui.example.conf" "$CONFIG"
fi

echo "==> udev rule"
sudo cp "$REPO/install/99-dwinui.rules" /etc/udev/rules.d/99-dwinui.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty

echo "==> systemd unit"
sed -e "s|@USER@|$USER|" -e "s|@VENV@|$VENV|" -e "s|@CONFIG@|$CONFIG|" \
    "$REPO/install/dwinui.service" | sudo tee /etc/systemd/system/dwinui.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable dwinui.service

ASVC="$PRINTER_DATA/moonraker.asvc"
if [ -f "$ASVC" ] && ! grep -qx dwinui "$ASVC"; then
    echo "==> allowing Moonraker to manage the service"
    echo dwinui >> "$ASVC"
fi

sudo systemctl restart dwinui.service
echo "Done. Logs: journalctl -u dwinui -f"
