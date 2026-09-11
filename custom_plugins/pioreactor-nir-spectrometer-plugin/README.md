# Pioreactor optical sweep plugin

This plugin uses an Adafruit AS7341 with two external Pioreactor LED channels to acquire two optical signals independently of the standard Pioreactor OD measurement:

1. **NIR transmission / apparent OD** using the AS7341 NIR detector and an external NIR LED.
2. **Blue-excited green signal** using a blue LED and the AS7341 515 nm channel for the intended ~520 nm emission/fluorescence measurement with the optical filter in the hardware.

The two sweeps are performed directly one after another. The job does not use OD dodging and does not wait for the standard Pioreactor OD cycle.

## Default sweep

`10, 20, 30, 40, 50, 60, 70, 80, 90, 100 %`

Each cycle performs:

```text
NIR 10 -> ... -> 100 %
then
blue excitation 10 -> ... -> 100 %
then
plateau/QC evaluation
then
one final NIR OD + one final 515 nm value
```

The cycle repeats according to `sweep_interval_s` (default 60 s).

## Automatic plateau selection

For each modality the plugin removes weak/saturated points, then searches for a contiguous region where signal versus LED intensity is approximately linear. A candidate plateau requires:

- at least `plateau_min_points` consecutive points,
- positive slope,
- `R² >= plateau_r2_min`,
- local-slope coefficient of variation `<= plateau_slope_cv_max`.

The longest qualifying plateau is preferred. The **three points closest to its centre** are used for the final value.

## NIR final value

For every selected intensity `P`:

```text
I0(P) = blank_lit(P) - blank_dark(P)
I(P)  = sample_lit(P) - sample_dark(P)
T(P)  = I(P) / I0(P)
OD(P) = -log10(T(P))
```

The final NIR value is the median of the three selected `OD(P)` values. Their MAD must remain below `nir_max_od_mad`; otherwise the sweep is rejected and the last valid UI value is retained while `nir_status` reports the failure.

For cell suspensions this is an apparent NIR OD/extinction because scattering contributes.

## Blue-excited 515 nm final value

The green measurement is not treated as transmission. The blank contains background from optical leakage/scattering/medium fluorescence, so it is subtracted:

```text
F_net(P) = sample_515(P) - blank_515(P)
```

For the three central plateau points the signal is normalized to an equivalent reference excitation:

```text
F_eq(P) = F_net(P) * P_reference / P
```

The default `P_reference` is 50 %. The final green value is the median of the three `F_eq(P)` values. This keeps the process time series comparable even if the automatically selected plateau moves to a different blue-LED intensity.

## Blank calibration from the UI

The plugin installs a second UI job:

**Optical Sweep Calibration** (`optical_sweep_calibration`)

With blank medium in the optical positions:

1. Stop `optical_sweep_reading` if active.
2. Start **Optical Sweep Calibration** in the Pioreactor UI.
3. The default action records NIR and blue-excited 515 nm blank curves across all configured intensities.
4. References are stored persistently with modality, LED channel, gain and intensity list.
5. Calibration points are also exported.

CLI equivalent:

```bash
pio run optical_sweep_calibration
```

Optional:

```bash
pio run optical_sweep_calibration --mode nir
pio run optical_sweep_calibration --mode green
```

A stored calibration is ignored if the LED channel, gain or configured intensity list changes. Recalibrate after changing any of those parameters.

## UI

The measurement job displays the current values:

- `nir_od`
- `nir_transmission`
- `green_signal`
- `nir_status`
- `green_status`
- `sweep_id`
- `last_sweep_timestamp`

Two overview time-series charts are installed:

- **Apparent NIR OD**
- **515 nm fluorescence**

Final numeric values are only updated after a valid completed sweep. Invalid sweeps remain stored with their QC status but do not overwrite the last valid current value.

## Exportable datasets

- `optical_sweep_points`: every intensity-resolved raw/blank/QC point for both modalities.
- `optical_sweep_results`: one final result row per paired cycle, including plateau bounds, selected intensities and QC metrics.
- `optical_sweep_calibrations`: every blank calibration point.

## Default hardware mapping

```ini
[optical_sweep_reading.config]
nir_led_channel=D
blue_led_channel=C
```

Change these to the actual dedicated LED outputs. The plugin only changes those two channels during the sweeps. The AS7341 remains on I2C at `0x39`.

The green channel uses `sensor.channel_515nm`. The result is an instrument signal in arbitrary units, not a molecular fluorescence yield; filter, geometry, blue LED spectrum and detector position must remain unchanged after blank calibration.

## Configuration defaults

```ini
[optical_sweep_reading.config]
nir_led_channel=D
blue_led_channel=C
intensity_levels_pct=10,20,30,40,50,60,70,80,90,100
sweep_interval_s=60.0
settle_time_s=0.10
averages_per_point=1
calibration_averages_per_point=3
nir_gain=10
green_gain=10
minimum_signal_raw=25
minimum_green_net_raw=10
saturation_raw=65000
plateau_min_points=4
plateau_r2_min=0.995
plateau_slope_cv_max=0.10
nir_max_od_mad=0.03
nir_max_transmission=1.20
green_reference_intensity_pct=50.0
green_max_relative_mad=0.10
```

These are initial engineering defaults. Refine them later from dilution-series and repeatability data from the real reactor geometry.

## Offline installation over SSH

On the Internet-connected PC/WSL:

```bash
cd ~
rm -rf pioreactor-optical-transfer
git clone --depth 1 \
  --branch feature/nir-spectrometer-plugin \
  --single-branch \
  https://github.com/Tobieausb/pioreactor.git \
  pioreactor-optical-transfer

cd ~/pioreactor-optical-transfer/custom_plugins
tar -czf ~/pioreactor-optical-sweep-plugin.tar.gz \
  pioreactor-nir-spectrometer-plugin
```

Transfer:

```bash
scp ~/pioreactor-optical-sweep-plugin.tar.gz \
  pioreactor@<PIO_HOST>:/home/pioreactor/
```

On the Pi:

```bash
cd /home/pioreactor
rm -rf optical-plugin-src
mkdir -p optical-plugin-src
tar -xzf pioreactor-optical-sweep-plugin.tar.gz -C optical-plugin-src
cd optical-plugin-src/pioreactor-nir-spectrometer-plugin
pio plugins install pioreactor-nir-spectrometer-plugin --source .
```

The official `spectrometer-reading-plugin` must already be installed because this package reuses its AS7341 driver.

## Preflight

```bash
cd /home/pioreactor/optical-plugin-src/pioreactor-nir-spectrometer-plugin
chmod +x scripts/preflight_check.sh
./scripts/preflight_check.sh
```

It checks both `channel_nir` and `channel_515nm` plus the two new commands.

## First test

1. Confirm AS7341 at `0x39`.
2. Confirm `nir_led_channel` and `blue_led_channel` match the wiring.
3. Insert blank medium.
4. Run **Optical Sweep Calibration** from the UI.
5. Inspect that both calibration curves respond sensibly without persistent saturation.
6. Replace blank with sample/culture.
7. Start **Optical Sweep Reading** from the UI or:

```bash
pio run optical_sweep_reading
```

Trust the final values only when the corresponding status is `ok`.
