# Hardware wiring and offline SSH installation

This guide covers the paired AS7341 optical-sweep plugin: NIR transmission/apparent OD plus blue-excited 515 nm measurement.

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

## Verify the AS7341

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

## Prepare the plugin on an Internet-connected PC / WSL

```bash
cd ~
rm -rf pioreactor-optical-transfer

git clone \
  --depth 1 \
  --branch feature/nir-spectrometer-plugin \
  --single-branch \
  https://github.com/Tobieausb/pioreactor.git \
  pioreactor-optical-transfer

cd ~/pioreactor-optical-transfer/custom_plugins
tar -czf ~/pioreactor-optical-sweep-plugin.tar.gz \
  pioreactor-nir-spectrometer-plugin
```

Transfer over the local network:

```bash
scp ~/pioreactor-optical-sweep-plugin.tar.gz \
  pioreactor@<PIO_HOST>:/home/pioreactor/
```

## Install on the offline Pioreactor

```bash
ssh pioreactor@<PIO_HOST>
cd /home/pioreactor
rm -rf optical-plugin-src
mkdir -p optical-plugin-src
tar -xzf pioreactor-optical-sweep-plugin.tar.gz -C optical-plugin-src
cd optical-plugin-src/pioreactor-nir-spectrometer-plugin
pio plugins install pioreactor-nir-spectrometer-plugin --source .
```

The official `spectrometer-reading-plugin` must already be installed because this plugin reuses its AS7341 driver:

```bash
pio plugins list | grep -i spectrometer
```

## Preflight

```bash
cd /home/pioreactor/optical-plugin-src/pioreactor-nir-spectrometer-plugin
chmod +x scripts/preflight_check.sh
./scripts/preflight_check.sh
```

Expected commands:

```text
optical_sweep_calibration
optical_sweep_reading
```

## Blank calibration from the UI

With blank medium in both optical measurement positions:

1. Stop **Optical Sweep Reading** if active.
2. Start **Optical Sweep Calibration** from the Pioreactor UI.
3. The plugin records the complete NIR 10-100% blank curve.
4. It then records the complete blue-excited 515 nm 10-100% blank curve.
5. Both calibrations are stored persistently with LED channel, gain and intensity-list metadata.

CLI equivalent:

```bash
pio run optical_sweep_calibration
```

The standard OD job does not need to be stopped for synchronization because the optical paths are spatially independent and this plugin does not use OD dodging. Only the configured NIR and blue LED channels are switched by this job.

## Measurement

Start **Optical Sweep Reading** from the UI or:

```bash
pio run optical_sweep_reading
```

Each cycle performs:

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
