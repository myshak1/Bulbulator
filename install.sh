#!/bin/sh
# Bulbulator installer for Debian / Ubuntu / Raspberry Pi OS / Armbian.
# Usage:  sudo ./install.sh
set -e
[ "$(id -u)" = 0 ] || { echo "Run as root: sudo ./install.sh"; exit 1; }
cd "$(dirname "$0")"

echo "== packages"
apt-get install -y --no-install-recommends python3 python3-opencv python3-numpy

echo "== files -> /opt/bulbulator"
install -d /opt/bulbulator/web
install -m 755 bulbulator.py /opt/bulbulator/bulbulator.py
install -m 644 web/index.html /opt/bulbulator/web/index.html

if [ -f /etc/systemd/system/bulbulator.service ]; then
    echo "== bulbulator.service already exists - keeping your settings"
else
    install -m 644 deploy/bulbulator.service /etc/systemd/system/bulbulator.service
fi
systemctl daemon-reload
systemctl enable --now bulbulator
systemctl restart bulbulator

sleep 3
systemctl --no-pager --lines=5 status bulbulator || true
echo
echo "Done. Edit the camera source in /etc/systemd/system/bulbulator.service"
echo "(BULBULATOR_STREAM), then: sudo systemctl daemon-reload && sudo systemctl restart bulbulator"
echo "Web page: http://$(hostname -I | awk '{print $1}'):8088/"
