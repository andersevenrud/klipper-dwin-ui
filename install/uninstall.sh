#!/usr/bin/env bash
# Remove what install/install.sh set up: the dwinui service, the udev rule,
# the moonraker.asvc entry and the virtualenv.
# Your settings (dwinui.conf), moonraker.conf and this repository are left
# alone; the script tells you what to remove yourself.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${VENV:-$HOME/dwinui-env}"
PRINTER_DATA="${PRINTER_DATA:-$HOME/printer_data}"
CONFIG="$PRINTER_DATA/config/dwinui.conf"
ASVC="$PRINTER_DATA/moonraker.asvc"

if [ "${1:-}" != "-y" ]; then
    read -r -p "Uninstall dwinui (service, udev rule, virtualenv)? [y/N] " answer
    case "$answer" in [yY]*) ;; *) echo "Nothing changed."; exit 0 ;; esac
fi

echo "==> service"
if [ -f /etc/systemd/system/dwinui.service ]; then
    sudo systemctl disable --now dwinui.service || true
    sudo rm /etc/systemd/system/dwinui.service
    sudo systemctl daemon-reload
fi

echo "==> udev rule"
if [ -f /etc/udev/rules.d/99-dwinui.rules ]; then
    sudo rm /etc/udev/rules.d/99-dwinui.rules
    sudo udevadm control --reload-rules
fi

ASVC_REMOVED=0
if [ -f "$ASVC" ] && grep -qx dwinui "$ASVC"; then
    echo "==> moonraker.asvc"
    sed -i '/^dwinui$/d' "$ASVC"
    ASVC_REMOVED=1
fi

echo "==> virtualenv $VENV"
rm -rf "$VENV"

echo
echo "dwinui is uninstalled. Left for you to remove, if you want:"
[ -f "$CONFIG" ] && echo "  - your settings:          rm $CONFIG"
grep -qs "^\[update_manager dwinui\]" "$PRINTER_DATA/config/moonraker.conf" &&
    echo "  - the [update_manager dwinui] section in moonraker.conf"
for boot in /boot/firmware/config.txt /boot/config.txt; do
    if grep -qs "^# >>> dwinui$" "$boot"; then
        echo "  - the Pi GPIO setup: the lines between '# >>> dwinui' and '# <<< dwinui'"
        echo "    in $boot (then reboot)"
    fi
done
echo "  - this repository:        rm -rf $REPO"
if [ "$ASVC_REMOVED" = 1 ]; then
    echo
    echo "Restart Moonraker once (when not printing) so it forgets the service:"
    echo "  sudo systemctl restart moonraker"
fi
