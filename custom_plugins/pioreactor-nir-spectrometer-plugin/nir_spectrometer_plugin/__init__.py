# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import suppress
from math import isfinite
from math import log10
from statistics import mean
from time import sleep
from typing import Any

import board
import click
from msgspec.json import decode
from pioreactor import pubsub
from pioreactor import types as pt
from pioreactor.actions import led_intensity as led_utils
from pioreactor.background_jobs.base import BackgroundJobWithDodgingContrib
from pioreactor.background_jobs.leader.mqtt_to_db_streaming import TopicToParserToTable
from pioreactor.background_jobs.leader.mqtt_to_db_streaming import produce_metadata
from pioreactor.background_jobs.leader.mqtt_to_db_streaming import register_source_to_sink
from pioreactor.cli.run import run
from pioreactor.config import config
from pioreactor.exc import HardwareNotFoundError
from pioreactor.utils import is_pio_job_running
from pioreactor.utils import local_persistent_storage
from pioreactor.utils.timing import current_utc_datetime
from pioreactor.utils.timing import RepeatedTimer
from pioreactor.whoami import get_assigned_experiment_name
from pioreactor.whoami import get_unit_name
from spectrometer_reading_plugin._vendor import adafruit_as7341

PLUGIN_NAME = "nir_spectrometer_plugin"
JOB_NAME = "nir_spectrometer_reading"
BLANK_CACHE = "nir_spectrometer_blank"
MEASUREMENT_TOPIC = "nir_spectrometer_reading/measurement"
DEFAULT_LEVELS = tuple(range(10, 101, 10))


def _parse_levels(raw: str) -> tuple[int, ...]:
    try:
        levels = tuple(int(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError("intensity_levels_pct must be a comma-separated list of integers") from exc

    if not levels:
        raise ValueError("intensity_levels_pct must contain at least one intensity")
    if any(level < 0 or level > 100 for level in levels):
        raise ValueError("all intensity levels must be between 0 and 100 percent")
    if len(set(levels)) != len(levels):
        raise ValueError("intensity_levels_pct must not contain duplicates")
    return levels


def _gain_time_normalize(sensor: Any, reading: int) -> float:
    # Keep the same normalization convention as Pioreactor's official
    # spectrometer-reading-plugin so datasets remain directly comparable.
    return float(reading) / (2 ** (sensor.gain - 1)) / sensor.atime


def _measurement_parser(topic: str, payload: pt.MQTTMessagePayload) -> dict[str, Any]:
    metadata = produce_metadata(topic)
    values = decode(payload)
    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": values["timestamp"],
        "sweep_id": int(values["sweep_id"]),
        "point_index": int(values["point_index"]),
        "led_channel": str(values["led_channel"]),
        "led_intensity_pct": float(values["led_intensity_pct"]),
        "dark_raw": int(values["dark_raw"]),
        "lit_raw": int(values["lit_raw"]),
        "signal_raw": float(values["signal_raw"]),
        "signal_normalized": float(values["signal_normalized"]),
        "blank_signal": values.get("blank_signal"),
        "transmission": values.get("transmission"),
        "nir_od": values.get("nir_od"),
        "valid": int(bool(values["valid"])),
        "status": str(values["status"]),
    }


register_source_to_sink(
    TopicToParserToTable(
        "pioreactor/+/+/nir_spectrometer_reading/measurement",
        _measurement_parser,
        "nir_spectrometer_readings",
    )
)


def _blank_key(unit: str, led_channel: str) -> str:
    return f"{unit}:{led_channel}"


def _load_blank(unit: str, led_channel: str) -> dict[str, float]:
    with local_persistent_storage(BLANK_CACHE) as cache:
        raw = cache.get(_blank_key(unit, led_channel))
    if raw is None:
        return {}
    return {str(k): float(v) for k, v in decode(raw).items()}


def _make_sensor() -> Any:
    try:
        sensor = adafruit_as7341.AS7341(board.I2C())
    except Exception as exc:
        raise HardwareNotFoundError("AS7341 not detected on the Pioreactor I2C bus") from exc

    sensor.gain = config.getint("nir_spectrometer.config", "gain", fallback=10)
    return sensor


def _read_nir_raw(sensor: Any, averages: int) -> int:
    values = [int(sensor.channel_nir) for _ in range(max(1, averages))]
    return int(round(mean(values)))


def _measure_point(
    sensor: Any,
    unit: str,
    experiment: str,
    led_channel: str,
    intensity_pct: float,
    settle_time_s: float,
    averages: int,
    source_of_event: str,
) -> tuple[int, int]:
    all_off = {channel: 0.0 for channel in led_utils.ALL_LED_CHANNELS}
    lit_state = dict(all_off)
    lit_state[led_channel] = float(intensity_pct)

    with led_utils.lock_leds_temporarily(list(led_utils.ALL_LED_CHANNELS)) as lock_owner:
        with led_utils.change_leds_intensities_temporarily(
            all_off,
            unit=unit,
            experiment=experiment,
            source_of_event=source_of_event,
            pubsub_client=None,
            verbose=False,
            lock_owner=lock_owner,
        ):
            sleep(settle_time_s)
            dark_raw = _read_nir_raw(sensor, averages)

            with led_utils.change_leds_intensities_temporarily(
                lit_state,
                unit=unit,
                experiment=experiment,
                source_of_event=source_of_event,
                pubsub_client=None,
                verbose=False,
                lock_owner=lock_owner,
            ):
                sleep(settle_time_s)
                lit_raw = _read_nir_raw(sensor, averages)

    return dark_raw, lit_raw


class NirSpectrometerReading(BackgroundJobWithDodgingContrib):
    job_name = JOB_NAME

    published_settings = {
        "nir_signal": {"datatype": "float", "unit": "AU", "settable": False},
        "nir_od": {"datatype": "float", "unit": "AU", "settable": False},
        "intensity_pct": {"datatype": "float", "unit": "%", "settable": False},
        "sweep_id": {"datatype": "integer", "settable": False},
        "valid": {"datatype": "boolean", "settable": False},
        "status": {"datatype": "string", "settable": False},
    }

    def __init__(self, unit: str, experiment: str, enable_dodging_od: bool = True) -> None:
        super().__init__(
            unit=unit,
            experiment=experiment,
            enable_dodging_od=enable_dodging_od,
            plugin_name=PLUGIN_NAME,
        )

        self.sensor = _make_sensor()
        self.led_channel = config.get("nir_spectrometer.config", "led_channel", fallback="D").upper()
        if self.led_channel not in led_utils.ALL_LED_CHANNELS:
            self.clean_up()
            raise ValueError(f"Invalid LED channel {self.led_channel!r}; expected A, B, C, or D")

        self.levels = _parse_levels(
            config.get(
                "nir_spectrometer.config",
                "intensity_levels_pct",
                fallback=",".join(str(v) for v in DEFAULT_LEVELS),
            )
        )
        self.settle_time_s = config.getfloat("nir_spectrometer.config", "settle_time_s", fallback=0.15)
        self.averages = config.getint("nir_spectrometer.config", "averages_per_point", fallback=3)
        self.min_signal_raw = config.getfloat("nir_spectrometer.config", "minimum_signal_raw", fallback=25.0)
        self.saturation_raw = config.getint("nir_spectrometer.config", "saturation_raw", fallback=65000)
        self.blank = _load_blank(unit, self.led_channel)

        self.point_index = 0
        self.sweep_id = 0
        self.nir_signal = 0.0
        self.nir_od = 0.0
        self.intensity_pct = float(self.levels[0])
        self.valid = False
        self.status = "waiting"
        self.continuous_sampling_timer: RepeatedTimer | None = None

        if not self.blank:
            self.logger.warning(
                "No NIR blank reference found. Raw transmission will be recorded, but nir_od will remain invalid. "
                "Run `pio run nir_spectrometer_blank` with blank medium before the experiment."
            )

    def on_disconnected(self) -> None:
        super().on_disconnected()
        with suppress(AttributeError):
            self.continuous_sampling_timer.cancel()

    def action_to_do_before_od_reading(self) -> None:
        # BackgroundJobWithDodgingContrib pauses this job around the standard OD reading.
        return None

    def action_to_do_after_od_reading(self) -> None:
        self._record_one_point()

    def _record_one_point(self) -> None:
        intensity = float(self.levels[self.point_index])
        dark_raw, lit_raw = _measure_point(
            self.sensor,
            self.unit,
            self.experiment,
            self.led_channel,
            intensity,
            self.settle_time_s,
            self.averages,
            self.job_name,
        )

        signal_raw = float(lit_raw - dark_raw)
        signal_normalized = _gain_time_normalize(self.sensor, max(0, int(round(signal_raw))))
        blank_signal = self.blank.get(str(int(intensity)))

        valid = True
        status = "ok"
        transmission: float | None = None
        nir_od: float | None = None

        if lit_raw >= self.saturation_raw:
            valid = False
            status = "saturated"
        elif signal_raw <= self.min_signal_raw:
            valid = False
            status = "signal_too_low"
        elif blank_signal is None or blank_signal <= 0:
            valid = False
            status = "no_blank"
        else:
            transmission = signal_raw / blank_signal
            if transmission <= 0 or not isfinite(transmission):
                valid = False
                status = "invalid_transmission"
            else:
                nir_od = -log10(transmission)
                if not isfinite(nir_od):
                    valid = False
                    status = "invalid_od"

        timestamp = current_utc_datetime().isoformat()
        payload = {
            "timestamp": timestamp,
            "sweep_id": self.sweep_id,
            "point_index": self.point_index,
            "led_channel": self.led_channel,
            "led_intensity_pct": intensity,
            "dark_raw": dark_raw,
            "lit_raw": lit_raw,
            "signal_raw": signal_raw,
            "signal_normalized": signal_normalized,
            "blank_signal": blank_signal,
            "transmission": transmission,
            "nir_od": nir_od,
            "valid": valid,
            "status": status,
        }

        self.intensity_pct = intensity
        self.nir_signal = signal_normalized
        self.valid = valid
        self.status = status
        if nir_od is not None:
            self.nir_od = nir_od

        self.publish(f"pioreactor/{self.unit}/{self.experiment}/{MEASUREMENT_TOPIC}", payload)

        self.point_index += 1
        if self.point_index >= len(self.levels):
            self.point_index = 0
            self.sweep_id += 1

    def _record_continuously(self) -> None:
        if self.state != self.READY or self.currently_dodging_od:
            return
        self._record_one_point()

    def initialize_dodging_operation(self) -> None:
        with suppress(AttributeError):
            self.continuous_sampling_timer.cancel()

    def initialize_continuous_operation(self) -> None:
        with suppress(AttributeError):
            self.continuous_sampling_timer.cancel()

        interval_s = config.getfloat("nir_spectrometer.config", "continuous_interval_s", fallback=5.0)
        if interval_s <= 0:
            self.logger.error("continuous_interval_s must be > 0")
            self.clean_up()
            return

        self.continuous_sampling_timer = RepeatedTimer(
            interval_s,
            self._record_continuously,
            job_name=self.job_name,
            run_immediately=True,
            logger=self.logger,
        ).start()


@run.command(name=JOB_NAME)
def start_nir_spectrometer_reading() -> None:
    """Start stepped NIR transmission / apparent-OD measurements."""
    unit = get_unit_name()
    experiment = get_assigned_experiment_name(unit)
    enable_dodging_od = config.getboolean("nir_spectrometer.config", "enable_dodging_od", fallback=True)
    job = NirSpectrometerReading(unit=unit, experiment=experiment, enable_dodging_od=enable_dodging_od)
    job.block_until_disconnected()


@run.command(name="nir_spectrometer_blank")
@click.option("--samples", default=None, type=int, help="Samples per LED level; overrides configuration.")
def capture_nir_blank(samples: int | None) -> None:
    """Capture and persist blank-medium NIR references at every configured LED intensity."""
    unit = get_unit_name()
    experiment = get_assigned_experiment_name(unit)

    if is_pio_job_running("od_reading"):
        raise click.ClickException("Stop od_reading before capturing the NIR blank reference.")
    if is_pio_job_running(JOB_NAME):
        raise click.ClickException(f"Stop {JOB_NAME} before capturing the NIR blank reference.")

    sensor = _make_sensor()
    led_channel = config.get("nir_spectrometer.config", "led_channel", fallback="D").upper()
    levels = _parse_levels(
        config.get(
            "nir_spectrometer.config",
            "intensity_levels_pct",
            fallback=",".join(str(v) for v in DEFAULT_LEVELS),
        )
    )
    settle_time_s = config.getfloat("nir_spectrometer.config", "settle_time_s", fallback=0.15)
    averages = samples or config.getint("nir_spectrometer.config", "blank_averages_per_point", fallback=5)

    references: dict[str, float] = {}
    click.echo(f"Capturing NIR blank on LED channel {led_channel}: {levels}")

    for intensity in levels:
        dark_raw, lit_raw = _measure_point(
            sensor,
            unit,
            experiment,
            led_channel,
            float(intensity),
            settle_time_s,
            averages,
            "nir_spectrometer_blank",
        )
        signal = float(lit_raw - dark_raw)
        if signal <= 0:
            raise click.ClickException(
                f"Blank signal at {intensity}% is <= 0 ({signal}). Check LED orientation, wiring, and AS7341 placement."
            )
        references[str(intensity)] = signal
        click.echo(f"  {intensity:>3}%: dark={dark_raw}, lit={lit_raw}, corrected={signal:.1f}")

    with local_persistent_storage(BLANK_CACHE) as cache:
        cache[_blank_key(unit, led_channel)] = pubsub.dumps(references)

    click.echo("NIR blank reference stored successfully.")
