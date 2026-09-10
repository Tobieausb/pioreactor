# Pioreactor NIR Spectrometer Plugin

This plugin extends a Pioreactor equipped with the Adafruit AS7341 multispectral sensor so that an **external Pioreactor LED channel** can be used for stepped NIR transmission measurements.

The intended hardware is:

- Adafruit AS7341 / Pioreactor spectrometer hardware on I2C.
- An additional NIR LED connected to one Pioreactor LED output, default **channel D**.
- The NIR LED arranged in transmission geometry through the culture toward the AS7341.
- The official `spectrometer-reading-plugin` installed, because this package reuses its AS7341 driver.

The AS7341 already exposes a dedicated NIR detector channel. This plugin reads that channel while driving the external NIR LED.

## Measurement concept

The default intensity sequence is:

`10, 20, 30, 40, 50, 60, 70, 80, 90, 100 %`

When OD dodging is enabled, **one NIR intensity point is measured after each normal Pioreactor OD reading**. After 100 %, the sequence starts again at 10 % and `sweep_id` is incremented.

Each point contains:

- `dark_raw`: AS7341 NIR reading with all Pioreactor LEDs off.
- `lit_raw`: AS7341 NIR reading with the external NIR LED on.
- `signal_raw = lit_raw - dark_raw`.
- `signal_normalized`: signal normalized using the same gain/time convention as the official spectrometer plugin.
- `blank_signal`: stored blank-medium reference at the same LED intensity.
- `transmission = signal_raw / blank_signal`.
- `nir_od = -log10(transmission)`.
- `valid` and `status` quality-control fields.

For cell suspensions this should be interpreted as an **apparent NIR optical density / extinction**, because scattering contributes to the measurement.

## Files added by the plugin

- Python background job and blank command.
- Pioreactor job UI descriptor.
- NIR signal and apparent-NIR-OD charts.
- SQLite table `nir_spectrometer_readings`.
- Exportable dataset `nir_spectrometer_readings`.
- Additional Pioreactor configuration.
- Example experiment profile.

## Important hardware configuration

Before switching on the NIR LED, confirm which physical Pioreactor LED output is connected to it.

The default is:

```ini
[nir_spectrometer_reading.config]
led_channel=D
```

If the additional NIR LED is connected to A, B or C, change this value before running the blank or measurement job.

Do **not** use a channel that is simultaneously required by another illumination function during the NIR measurement.

## Install directly from source over SSH

The easiest test workflow is to clone this branch on the Pioreactor and install the plugin source folder with Pioreactor's plugin installer. This is preferable to plain `pip install`, because Pioreactor must also install the SQL, config, UI and export descriptors.

```bash
cd ~
git clone --branch feature/nir-spectrometer-plugin --single-branch https://github.com/Tobieausb/pioreactor.git pioreactor-nir-test
cd ~/pioreactor-nir-test/custom_plugins/pioreactor-nir-spectrometer-plugin
pio plugins install pioreactor-nir-spectrometer-plugin --source .
```

If the repository already exists on the Pi:

```bash
cd ~/pioreactor
git fetch origin
git switch feature/nir-spectrometer-plugin
git pull
cd custom_plugins/pioreactor-nir-spectrometer-plugin
pio plugins install pioreactor-nir-spectrometer-plugin --source .
```

For a cluster, install the package on every worker that should acquire NIR data and on the leader so the SQL/export/chart resources are present there.

## Verify plugin discovery

After installation:

```bash
pio plugins list
pio run --help | grep nir
```

You should see commands corresponding to:

- `nir_spectrometer_reading`
- `nir_spectrometer_blank`

## Check I2C / AS7341 first

Before testing LED control, verify that the spectrometer is visible on I2C:

```bash
i2cdetect -y 1
```

The AS7341 normally uses address `0x39`.

You can also confirm the Python driver can instantiate the sensor:

```bash
python3 - <<'PY'
import board
from spectrometer_reading_plugin._vendor import adafruit_as7341
sensor = adafruit_as7341.AS7341(board.I2C())
print("AS7341 detected")
print("NIR raw:", sensor.channel_nir)
PY
```

## First hardware test: blank medium

1. Fill the reactor/vial with the medium that will serve as the optical blank.
2. Make sure the normal OD job and the NIR measurement job are stopped.
3. Confirm the configured NIR LED channel.
4. Run:

```bash
pio run nir_spectrometer_blank
```

The command steps through all configured intensities and prints values such as:

```text
10%: dark=..., lit=..., corrected=...
20%: dark=..., lit=..., corrected=...
...
100%: dark=..., lit=..., corrected=...
```

The corrected signal should generally rise with increasing LED intensity. The command aborts if the light-corrected blank signal is non-positive.

The reference is stored persistently on the Pi, keyed by Pioreactor unit and LED channel.

## First culture / test measurement

Start the normal OD job as usual and then start the NIR job:

```bash
pio run nir_spectrometer_reading
```

With the default `enable_dodging_od=True`, the NIR job synchronizes to the standard OD cycle and measures one NIR intensity point after each OD reading.

The sequence is:

```text
normal OD reading
-> NIR 10 % point
normal OD reading
-> NIR 20 % point
...
normal OD reading
-> NIR 100 % point
-> next sweep starts at 10 %
```

This intentionally avoids blocking the normal OD acquisition with a long ten-point sequence.

## Quality status

`status` can be:

- `ok`: valid blank-normalized NIR OD.
- `saturated`: AS7341 reading reached the configured saturation threshold.
- `signal_too_low`: dark-corrected signal is below the configured minimum.
- `no_blank`: no blank exists for that intensity.
- `invalid_transmission`: transmission could not be calculated safely.
- `invalid_od`: logarithmic conversion produced an invalid result.

Raw data are still retained even if a point is invalid.

## Configuration

Defaults:

```ini
[nir_spectrometer_reading.config]
led_channel=D
intensity_levels_pct=10,20,30,40,50,60,70,80,90,100
enable_dodging_od=True
pre_delay_duration=1.5
post_delay_duration=0.5
settle_time_s=0.15
averages_per_point=3
blank_averages_per_point=5
continuous_interval_s=5.0
gain=10
minimum_signal_raw=25
saturation_raw=65000
```

### Recommended settings for the first test

Keep the default 10-% steps. If the upper intensities saturate, do not immediately lower the AS7341 gain; first confirm that the lower intensity points provide a useful dynamic range. Later the sweep can be used to select the best intensity range automatically.

## Data export

The leader stores every NIR point in:

`nir_spectrometer_readings`

The Export Data page exposes the dataset:

`NIR spectrometer sweep readings`

The export contains all intensity-resolved raw and processed data and can be combined by timestamp with standard Pioreactor OD/process exports.

Important columns include:

```text
experiment
pioreactor_unit
timestamp
sweep_id
point_index
led_channel
led_intensity_pct
dark_raw
lit_raw
signal_raw
signal_normalized
blank_signal
transmission
nir_od
valid
status
```

## Experiment profile

An example profile is included at:

`examples/nir_od_profile.yaml`

The blank is intentionally **not** part of the experiment profile because blank acquisition should be performed manually with known blank medium before starting the culture experiment.

## Before tomorrow's test

The minimum recommended sequence is:

```bash
# 1. Install plugin
pio plugins install pioreactor-nir-spectrometer-plugin --source .

# 2. Confirm AS7341
sudo i2cdetect -y 1

# 3. Confirm/edit LED channel in Pioreactor config

# 4. With blank medium and od_reading stopped
pio run nir_spectrometer_blank

# 5. Start normal experiment OD and then
pio run nir_spectrometer_reading
```

If the 10 -> 100 % blank readings do not increase sensibly, stop there and check optical geometry / LED wiring before interpreting OD values.
