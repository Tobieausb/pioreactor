#!/bin/bash
set -euo pipefail

echo "=== Pioreactor NIR spectrometer preflight ==="
echo

echo "[1/5] Pioreactor version"
pio version || true
echo

echo "[2/5] Plugin discovery"
pio plugins list | grep -Ei 'nir|spectrometer' || true
echo

echo "[3/5] I2C bus (expect AS7341 at 0x39)"
sudo i2cdetect -y 1
echo

echo "[4/5] AS7341 NIR channel"
python3 - <<'PY'
import board
from spectrometer_reading_plugin._vendor import adafruit_as7341
sensor = adafruit_as7341.AS7341(board.I2C())
print("AS7341 detected")
print("NIR raw:", sensor.channel_nir)
PY

echo
echo "[5/5] NIR commands"
pio run --help | grep -E 'nir_spectrometer_(reading|blank)' || {
  echo "NIR commands were not discovered. Reinstall the plugin with pio plugins install ... --source ."
  exit 1
}

echo
echo "Preflight passed. Next: fill with blank medium and run: pio run nir_spectrometer_blank"
