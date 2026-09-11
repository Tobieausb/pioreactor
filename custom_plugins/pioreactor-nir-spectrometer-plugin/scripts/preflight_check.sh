#!/bin/bash
set -euo pipefail

echo "=== Pioreactor optical sweep preflight ==="
echo

echo "[1/5] Pioreactor version"
pio version || true
echo

echo "[2/5] Plugin discovery"
pio plugins list | grep -Ei 'nir|spectrometer|optical' || true
echo

echo "[3/5] I2C bus (expect AS7341 at 0x39)"
sudo i2cdetect -y 1
echo

echo "[4/5] AS7341 NIR and 515 nm channels"
python3 - <<'PY'
import board
from spectrometer_reading_plugin._vendor import adafruit_as7341
sensor = adafruit_as7341.AS7341(board.I2C())
print("AS7341 detected")
print("NIR raw:", sensor.channel_nir)
print("515 nm raw:", sensor.channel_515nm)
PY

echo
echo "[5/5] Optical sweep commands"
pio run --help | grep -E 'optical_sweep_(reading|calibration)' || {
  echo "Optical sweep commands were not discovered. Reinstall the plugin with pio plugins install ... --source ."
  exit 1
}

echo
echo "Preflight passed. Next: place blank medium and run Optical Sweep Calibration from the UI or:"
echo "  pio run optical_sweep_calibration"
