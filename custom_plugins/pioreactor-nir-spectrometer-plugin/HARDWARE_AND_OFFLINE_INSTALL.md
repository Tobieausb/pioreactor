# Hardware wiring and complete offline installation

This guide covers the paired AS7341 optical-sweep plugin: NIR transmission/apparent OD plus blue-excited 515 nm measurement.

## Important dependency finding

The AS7341 driver is **not part of the normal Pioreactor core repository/package**.

It is vendored by the separate official Pioreactor plugin:

```text
spectrometer-reading-plugin/
└── spectrometer_reading_plugin/
    └── _vendor/
        └── adafruit_as7341.py
```

The custom optical-sweep plugin imports the driver from:

```python
from spectrometer_reading_plugin._vendor import adafruit_as7341
```

Therefore an offline installation must also provide the official `spectrometer-reading-plugin` unless it is already installed on the target Pioreactor.

The official plugin declares these Python dependencies:

- `Adafruit-Blinka`
- `adafruit-circuitpython-busdevice`
- `adafruit-circuitpython-register`

Pioreactor worker installations already include Blinka and BusDevice explicitly. `adafruit-circuitpython-register` is not explicitly pinned in the normal Pioreactor worker requirements, so this guide also transfers that package as an offline wheel.

## AS7341 connection through the pin header

The AS7341 remains a standard I2C device at address `0x39`.

| AS7341 pin | Raspberry Pi / Pioreactor | Physical Pi pin |
|---|---|---:|
| `VIN` | 3.3 V | 1 or 17 |
| `GND` | GND | 6 or another GND |
| `SDA` | GPIO2 / SDA1 | 3 |
| `SCL` | GPIO3 / SCL1 | 5 |

Leave `INT` and `GPIO` unconnected.

With the Pioreactor HAT installed, the preferred route is the HAT I2C/STEMMA connector to a JST-SH-to-Dupont breakout and then onto the AS7341 header. Match signals by name: 3.3 V -> VIN, GND -> GND, SDA -> SDA, SCL -> SCL.

The spectrometer uses I2C and does not consume the PWM/MOSFET outputs used for compressor, valves or pumps.

## Dedicated optical LEDs

Defaults:

```ini
[optical_sweep_reading.config]
nir_led_channel=D
blue_led_channel=C
```

- NIR LED: transmission geometry toward the AS7341 NIR detector.
- Blue LED: excitation for the filtered green measurement read on AS7341 `channel_515nm`.
- The two LED channels must be different.
- Do not move compressor or valve wiring to match these defaults; instead change the two channel assignments in the Pioreactor config.
- The standard OD optical path is independent and does not need to be synchronized with this plugin.

## Safe connection procedure

```bash
sudo shutdown -h now
```

Remove power, wire the sensor and LEDs, check polarity and signal names, then power the Pioreactor again.

---

# Complete offline software preparation

All Internet-dependent commands in this section are run on the Internet-connected PC / WSL machine, not on the Pioreactor.

## 1. Create a clean transfer directory on the PC

```bash
cd ~
rm -rf pioreactor-optical-offline
mkdir -p pioreactor-optical-offline/src
mkdir -p pioreactor-optical-offline/wheelhouse
```

## 2. Download the custom optical-sweep plugin

```bash
git clone \
  --depth 1 \
  --branch feature/nir-spectrometer-plugin \
  --single-branch \
  https://github.com/Tobieausb/pioreactor.git \
  ~/pioreactor-optical-offline/src/pioreactor-custom
```

The plugin source will then be at:

```text
~/pioreactor-optical-offline/src/pioreactor-custom/
  custom_plugins/pioreactor-nir-spectrometer-plugin/
```

## 3. Download the official Pioreactor spectrometer plugin

This is required because it contains the vendored AS7341 driver.

```bash
git clone \
  --depth 1 \
  https://github.com/Pioreactor/spectrometer-reading-plugin.git \
  ~/pioreactor-optical-offline/src/spectrometer-reading-plugin
```

Confirm that the driver is present:

```bash
ls -l \
  ~/pioreactor-optical-offline/src/spectrometer-reading-plugin/spectrometer_reading_plugin/_vendor/adafruit_as7341.py
```

## 4. Download the missing Register dependency for offline installation

The standard Pioreactor worker already supplies Blinka and BusDevice. To make the installation robust even when `adafruit_register` is not already present, download its wheel explicitly:

```bash
python3 -m pip download \
  --no-deps \
  --dest ~/pioreactor-optical-offline/wheelhouse \
  adafruit-circuitpython-register
```

The Register package itself relies on Blinka, BusDevice, CircuitPython typing and `typing-extensions`. These are normally already available on a Pioreactor worker. The target Pi is checked before installation below.

## 5. Package the whole offline bundle

```bash
cd ~
tar -czf pioreactor-optical-offline.tar.gz \
  pioreactor-optical-offline
```

Check it:

```bash
ls -lh ~/pioreactor-optical-offline.tar.gz
```

## 6. Transfer the complete bundle to the Pioreactor over the local network

```bash
scp ~/pioreactor-optical-offline.tar.gz \
  pioreactor@<PIO_HOST>:/home/pioreactor/
```

The Pioreactor itself does not need Internet access for this transfer.

---

# Offline installation on the Pioreactor

## 7. Connect and unpack

```bash
ssh pioreactor@<PIO_HOST>
```

Then:

```bash
cd /home/pioreactor
rm -rf pioreactor-optical-offline
tar -xzf pioreactor-optical-offline.tar.gz
cd /home/pioreactor/pioreactor-optical-offline
```

## 8. Check which prerequisites are already installed

```bash
python3 - <<'PY'
modules = [
    "board",
    "adafruit_bus_device",
    "adafruit_register",
]
for module in modules:
    try:
        __import__(module)
        print(f"{module}: OK")
    except Exception as exc:
        print(f"{module}: MISSING ({exc})")
PY
```

On a normal Pioreactor worker, `board`/Blinka and `adafruit_bus_device` should already be present.

If `adafruit_register` reports `MISSING`, install the transferred wheel completely offline:

```bash
python3 -m pip install \
  --no-index \
  --no-deps \
  --find-links /home/pioreactor/pioreactor-optical-offline/wheelhouse \
  adafruit-circuitpython-register
```

Then rerun the import check.

## 9. Install the official spectrometer plugin from the transferred source

First check whether it is already installed:

```bash
pio plugins list | grep -i spectrometer || true
```

Also test the exact driver import:

```bash
python3 - <<'PY'
try:
    from spectrometer_reading_plugin._vendor import adafruit_as7341
    print("AS7341 driver already available: OK")
except Exception as exc:
    print("AS7341 driver not installed yet:", exc)
PY
```

If the driver is not available, install the official plugin from the local source directory:

```bash
cd /home/pioreactor/pioreactor-optical-offline/src/spectrometer-reading-plugin
pio plugins install spectrometer-reading-plugin --source .
```

No GitHub access is required because the source is local and the required Register package was transferred beforehand.

Verify:

```bash
python3 - <<'PY'
from spectrometer_reading_plugin._vendor import adafruit_as7341
print("AS7341 driver import: OK")
PY
```

## 10. Install the custom paired optical-sweep plugin

```bash
cd /home/pioreactor/pioreactor-optical-offline/src/pioreactor-custom/custom_plugins/pioreactor-nir-spectrometer-plugin

pio plugins install \
  pioreactor-nir-spectrometer-plugin \
  --source .
```

The official spectrometer plugin is now already installed, so the custom package dependency can be satisfied without Internet access.

## 11. Reboot once after both plugin installations

```bash
sudo reboot
```

Reconnect after boot:

```bash
ssh pioreactor@<PIO_HOST>
```

---

# Hardware and plugin verification

## 12. Verify the AS7341 on I2C

```bash
i2cdetect -y 1
```

Expect `0x39`.

Then verify both detector channels:

```bash
python3 - <<'PY'
import board
from spectrometer_reading_plugin._vendor import adafruit_as7341
sensor = adafruit_as7341.AS7341(board.I2C())
print("NIR:", sensor.channel_nir)
print("515 nm:", sensor.channel_515nm)
PY
```

## 13. Verify plugin command discovery

```bash
pio plugins list | grep -Ei 'spectrometer|nir'
pio run --help | grep optical_sweep
```

Expected custom commands:

```text
optical_sweep_calibration
optical_sweep_reading
```

## 14. Run the supplied preflight

```bash
cd /home/pioreactor/pioreactor-optical-offline/src/pioreactor-custom/custom_plugins/pioreactor-nir-spectrometer-plugin
chmod +x scripts/preflight_check.sh
./scripts/preflight_check.sh
```

---

# Blank calibration

## 15. Confirm LED channel assignments before switching LEDs

Defaults are:

```ini
[optical_sweep_reading.config]
nir_led_channel=D
blue_led_channel=C
```

Inspect the installed configuration and change these values if your physical wiring differs. Do not move compressor or valve wiring merely to match the defaults.

## 16. Insert blank medium and run the calibration

From the UI start:

```text
Optical Sweep Calibration
```

or via CLI:

```bash
pio run optical_sweep_calibration
```

The plugin records:

```text
NIR 10 -> 20 -> ... -> 100 %
then
Blue 10 -> 20 -> ... -> 100 %
```

Both calibration sets are stored persistently with LED channel, gain and intensity-list metadata.

The standard OD job does not need to be stopped for synchronization because the optical paths are spatially independent and this plugin does not use OD dodging. Only the configured NIR and blue LED channels are switched by this job.

---

# Measurement and first tests

## 17. Blank self-test

Leave blank medium in place and start:

```bash
pio run optical_sweep_reading
```

Expected approximately:

```text
NIR transmission ~ 1
NIR apparent OD ~ 0
515 nm blank-corrected final signal ~ 0
```

Stop and diagnose before using a culture if the blank self-test is clearly inconsistent.

## 18. Sample test

Replace the blank with a sample and run:

```bash
pio run optical_sweep_reading
```

Each measurement cycle performs:

```text
NIR 10 -> 20 -> ... -> 100 %
then
Blue 10 -> 20 -> ... -> 100 %
then
plateau/QC evaluation
then
one final NIR OD + one final 515 nm signal
```

The UI shows the current final values and time-series charts. Trust a new value only when the corresponding `nir_status` or `green_status` is `ok`.

## 19. Recommended first validation experiment

Before a real culture, run several repeats of:

- blank
- approximately 25% sample concentration
- approximately 50%
- approximately 75%
- undiluted sample

Export the raw sweep points and final sweep results. Use these data to tune the plateau, saturation and variability thresholds before relying on the values for process control.
