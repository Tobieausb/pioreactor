# Hardware wiring and offline SSH installation

This guide is for the Pioreactor NIR spectrometer plugin in this repository. It covers the Adafruit AS7341 connected through its 0.1-inch pin header instead of the sensor-side STEMMA QT connector, and an offline installation in which the Pioreactor itself has no Internet access.

## 1. AS7341 electrical interface

The AS7341 is an I2C device with fixed address `0x39`.

Only four connections are required:

| AS7341 pin | Connect to Pioreactor / Raspberry Pi | Raspberry Pi signal | Physical Pi header pin |
|---|---|---|---:|
| `VIN` | 3.3 V | 3V3 | 1 or 17 |
| `GND` | GND | GND | 6 (or another GND pin) |
| `SDA` | I2C SDA | GPIO2 / SDA1 | 3 |
| `SCL` | I2C SCL | GPIO3 / SCL1 | 5 |

Leave the AS7341 `INT` and `GPIO` pins unconnected for this plugin.

Use **3.3 V on VIN** for the Raspberry Pi / Pioreactor connection. Do not connect the sensor to a Pioreactor PWM output.

### Preferred physical connection when the Pioreactor HAT is installed

The Pioreactor HAT already exposes 3.3 V, GND, SDA and SCL on its STEMMA QT / Qwiic connector. The sensor itself does not need to use its STEMMA connector: a JST-SH-to-Dupont breakout/pigtail can connect the HAT's I2C connector to the AS7341 header pins.

Wire the four conductors by **signal name**, not only by wire colour:

- HAT 3.3 V -> AS7341 `VIN`
- HAT GND -> AS7341 `GND`
- HAT SDA -> AS7341 `SDA`
- HAT SCL -> AS7341 `SCL`

This is electrically identical to a STEMMA-QT-to-STEMMA-QT cable, but uses the AS7341 pin header on the sensor side.

### Direct Raspberry Pi header alternative

If the physical 40-pin Raspberry Pi header is safely accessible without disturbing the Pioreactor HAT, the same bus can be reached directly:

- Pi physical pin 1 -> AS7341 `VIN`
- Pi physical pin 6 -> AS7341 `GND`
- Pi physical pin 3 / GPIO2 -> AS7341 `SDA`
- Pi physical pin 5 / GPIO3 -> AS7341 `SCL`

Do not wedge Dupont connectors between the HAT and Raspberry Pi. If the HAT occupies the header, use the HAT I2C connector or a proper stacking/breakout header.

## 2. Why this does not consume compressor / valve PWM channels

The AS7341 uses the Raspberry Pi's dedicated I2C lines GPIO2 and GPIO3. Standard Pioreactor HAT PWM outputs use other GPIO/PWM resources. Therefore the spectrometer should not be connected to, or consume, the PWM outputs used for pumps, compressors, valves, LEDs or stirring.

Before powering the sensor, verify the live hardware mapping on the target Pioreactor:

```bash
python3 - <<'PY'
from pioreactor import hardware
print("SDA GPIO:", hardware.get_sda_pin())
print("SCL GPIO:", hardware.get_scl_pin())
try:
    print("PWM map:", hardware.get_pwm_to_pin_map())
except Exception as exc:
    print("PWM map could not be printed:", exc)
PY
```

The expected I2C GPIOs on a standard v1.x HAT are GPIO2 = SDA and GPIO3 = SCL.

If any custom compressor or valve wiring has manually repurposed GPIO2 or GPIO3, correct that conflict before attaching the AS7341. Custom actuators should remain on their established PWM/MOSFET outputs, not on the I2C pins.

## 3. Power-off wiring procedure

1. Shut down the Pioreactor cleanly:

   ```bash
   sudo shutdown -h now
   ```

2. Remove power.
3. Connect the four AS7341 wires listed above.
4. Check that SDA and SCL are not swapped.
5. Check that VIN goes to 3.3 V and not to a PWM output.
6. Leave `INT` and `GPIO` disconnected.
7. Reapply power and SSH back into the Pioreactor.

## 4. Verify the I2C bus before installing the NIR plugin

Check whether `i2cdetect` exists:

```bash
command -v i2cdetect
```

Then scan bus 1:

```bash
i2cdetect -y 1
```

The AS7341 should appear at address:

```text
39
```

Other Pioreactor devices may also appear. `0x39` does not conflict with standard Pioreactor I2C addresses.

If `0x39` is absent, stop here and re-check wiring before starting any LED or measurement test.

## 5. Check whether the official spectrometer plugin dependency is already present

The current NIR plugin reuses the AS7341 driver shipped with Pioreactor's official `spectrometer-reading-plugin`.

Run:

```bash
pio plugins list | grep -i spectrometer || true
python3 - <<'PY'
try:
    from spectrometer_reading_plugin._vendor import adafruit_as7341
    print("official spectrometer driver: OK")
except Exception as exc:
    print("official spectrometer driver: MISSING")
    print(exc)
PY
```

If this prints `official spectrometer driver: OK`, the custom NIR plugin can be installed fully offline from the local source folder.

If it is missing, first transfer/install the official spectrometer plugin and its Python dependencies while offline, or prepare those dependencies on the Internet-connected PC before the test.

## 6. Prepare the custom plugin on an Internet-connected PC

These commands are intended for WSL/Linux on the PC.

```bash
cd ~
rm -rf pioreactor-nir-transfer

git clone \
  --depth 1 \
  --branch feature/nir-spectrometer-plugin \
  --single-branch \
  https://github.com/Tobieausb/pioreactor.git \
  pioreactor-nir-transfer

cd ~/pioreactor-nir-transfer/custom_plugins
ls -la pioreactor-nir-spectrometer-plugin
```

Optional: create one compressed transfer file:

```bash
cd ~/pioreactor-nir-transfer/custom_plugins
tar -czf ~/pioreactor-nir-spectrometer-plugin.tar.gz \
  pioreactor-nir-spectrometer-plugin
```

## 7. Copy the plugin to the offline Pioreactor over SSH

Replace `<PIO_HOST>` with the Pioreactor hostname or IP address.

Example hostnames are often reachable as `<unit>.local` on the same LAN.

From WSL/Linux on the PC:

```bash
scp ~/pioreactor-nir-spectrometer-plugin.tar.gz \
  pioreactor@<PIO_HOST>:/home/pioreactor/
```

Then connect:

```bash
ssh pioreactor@<PIO_HOST>
```

On the Pioreactor:

```bash
cd /home/pioreactor
rm -rf nir-plugin-src
mkdir -p nir-plugin-src

tar -xzf pioreactor-nir-spectrometer-plugin.tar.gz \
  -C nir-plugin-src

cd /home/pioreactor/nir-plugin-src/pioreactor-nir-spectrometer-plugin
```

You can also skip the tarball and use recursive SCP directly:

```bash
scp -r \
  ~/pioreactor-nir-transfer/custom_plugins/pioreactor-nir-spectrometer-plugin \
  pioreactor@<PIO_HOST>:/home/pioreactor/
```

## 8. Install the plugin locally, without GitHub access on the Pi

From the plugin directory on the Pioreactor:

```bash
cd /home/pioreactor/nir-plugin-src/pioreactor-nir-spectrometer-plugin
pio plugins install pioreactor-nir-spectrometer-plugin --source .
```

Pioreactor's plugin installer must be used rather than plain `pip install`, because the plugin also contains Pioreactor config, SQL, export and UI resources.

Verify installation:

```bash
pio plugins list | grep -Ei 'nir|spectrometer'
pio run --help | grep -E 'nir_spectrometer_(reading|blank)'
```

## 9. Run the supplied preflight check

```bash
cd /home/pioreactor/nir-plugin-src/pioreactor-nir-spectrometer-plugin
chmod +x scripts/preflight_check.sh
./scripts/preflight_check.sh
```

The preflight expects the AS7341 at `0x39` and attempts an NIR-channel read.

## 10. Verify the extra NIR LED channel before an optical sweep

The custom plugin defaults to Pioreactor LED channel `D`:

```ini
[nir_spectrometer_reading.config]
led_channel=D
```

Do not assume channel D is available if it is already assigned to another LED or actuator in the physical build.

Inspect the Pioreactor config:

```bash
grep -nA20 -B2 'nir_spectrometer_reading.config' \
  /home/pioreactor/.pioreactor/config.ini
```

If the added NIR LED is connected to another Pioreactor LED channel, edit the config:

```bash
nano /home/pioreactor/.pioreactor/config.ini
```

Use only the Pioreactor LED/PWM output that is physically connected to this new NIR LED. Do not move compressor or valve wiring just to obtain the default `D` assignment; change `led_channel` in software instead.

## 11. First optical test with blank medium

Stop the normal OD job before blank capture.

Check running jobs if required, then stop OD reading from the UI or CLI. With blank medium in the vial/reactor run:

```bash
pio run nir_spectrometer_blank
```

Expected sequence:

```text
10 % -> dark / illuminated / corrected
20 % -> dark / illuminated / corrected
...
100 % -> dark / illuminated / corrected
```

The corrected blank signal should generally increase as LED intensity increases.

If the signal is zero, negative, or does not respond to LED intensity, stop and check:

- LED polarity and selected Pioreactor LED channel
- optical alignment through the reactor
- AS7341 orientation
- SDA/SCL wiring
- `0x39` visibility on I2C

## 12. Start normal NIR acquisition

After a valid blank has been stored, start the normal Pioreactor OD job and then:

```bash
pio run nir_spectrometer_reading
```

With OD dodging enabled, one NIR intensity point is recorded after each normal OD measurement. The default sequence is 10, 20, ... 100 %, then a new `sweep_id` starts.

## 13. Useful troubleshooting commands

I2C scan:

```bash
i2cdetect -y 1
```

Direct AS7341 NIR read:

```bash
python3 - <<'PY'
import board
from spectrometer_reading_plugin._vendor import adafruit_as7341
sensor = adafruit_as7341.AS7341(board.I2C())
print("AS7341 NIR raw:", sensor.channel_nir)
PY
```

Plugin discovery:

```bash
pio plugins list
pio run --help | grep nir
```

Pioreactor logs:

```bash
pio logs -n 100
```

Check custom plugin config:

```bash
grep -nA20 -B2 'nir_spectrometer_reading.config' \
  /home/pioreactor/.pioreactor/config.ini
```

## 14. Recommended order for the first test day

1. Power off.
2. Wire AS7341 `VIN`, `GND`, `SDA`, `SCL` only.
3. Power on.
4. Confirm `0x39` with `i2cdetect -y 1`.
5. Confirm the official AS7341 driver import.
6. Copy the custom plugin to the Pi by `scp`.
7. Install locally with `pio plugins install ... --source .`.
8. Run `scripts/preflight_check.sh`.
9. Confirm the configured external NIR LED channel matches the real wiring and does not replace compressor/valve outputs.
10. Insert blank medium.
11. Run `pio run nir_spectrometer_blank`.
12. Only if the 10-100 % response is plausible: start normal OD plus `pio run nir_spectrometer_reading`.
