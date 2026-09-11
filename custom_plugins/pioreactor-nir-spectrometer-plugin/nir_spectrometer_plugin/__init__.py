# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import suppress
from json import dumps
from json import loads
from math import isfinite
from statistics import mean
from threading import Lock
from time import sleep
from typing import Any

import board
import click
from msgspec.json import decode
from msgspec.json import encode
from pioreactor import pubsub
from pioreactor import types as pt
from pioreactor.actions import led_intensity as led_utils
from pioreactor.background_jobs.base import BackgroundJobContrib
from pioreactor.background_jobs.leader.mqtt_to_db_streaming import TopicToParserToTable
from pioreactor.background_jobs.leader.mqtt_to_db_streaming import produce_metadata
from pioreactor.background_jobs.leader.mqtt_to_db_streaming import register_source_to_sink
from pioreactor.config import config
from pioreactor.exc import HardwareNotFoundError
from pioreactor.logging import create_logger
from pioreactor.utils import is_pio_job_running
from pioreactor.utils import local_persistent_storage
from pioreactor.utils import managed_lifecycle
from pioreactor.utils.timing import current_utc_datetime
from pioreactor.utils.timing import RepeatedTimer
from pioreactor.whoami import get_assigned_experiment_name
from pioreactor.whoami import get_unit_name
from spectrometer_reading_plugin._vendor import adafruit_as7341

from .analysis import FinalMetric
from .analysis import finalize_green
from .analysis import finalize_nir
from .analysis import find_linear_plateau

__plugin_name__ = "nir-spectrometer-plugin"
__plugin_version__ = "0.2.0"

PLUGIN_NAME = __plugin_name__
JOB_NAME = "optical_sweep_reading"
CALIBRATION_JOB_NAME = "optical_sweep_calibration"
CONFIG_SECTION = f"{JOB_NAME}.config"
CALIBRATION_CACHE = "optical_sweep_calibration"
POINT_TOPIC = f"{JOB_NAME}/point"
RESULT_TOPIC = f"{JOB_NAME}/result"
CALIBRATION_TOPIC = f"{CALIBRATION_JOB_NAME}/point"
DEFAULT_LEVELS = tuple(range(10, 101, 10))


def _parse_levels(raw: str) -> tuple[int, ...]:
    try:
        levels = tuple(int(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError("intensity_levels_pct must be a comma-separated list of integers") from exc
    if len(levels) < 4:
        raise ValueError("intensity_levels_pct must contain at least four levels")
    if any(level <= 0 or level > 100 for level in levels):
        raise ValueError("all intensity levels must be > 0 and <= 100 percent")
    if any(b <= a for a, b in zip(levels[:-1], levels[1:])):
        raise ValueError("intensity levels must be strictly increasing")
    return levels


def _configured_levels() -> tuple[int, ...]:
    return _parse_levels(
        config.get(
            CONFIG_SECTION,
            "intensity_levels_pct",
            fallback=",".join(str(v) for v in DEFAULT_LEVELS),
        )
    )


def _gain_time_normalize(sensor: Any, reading: float) -> float:
    return float(reading) / (2 ** (sensor.gain - 1)) / sensor.atime


def _point_parser(topic: str, payload: pt.MQTTMessagePayload) -> dict[str, Any]:
    metadata = produce_metadata(topic)
    values = decode(payload)
    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": values["timestamp"],
        "sweep_id": int(values["sweep_id"]),
        "modality": str(values["modality"]),
        "point_index": int(values["point_index"]),
        "led_channel": str(values["led_channel"]),
        "led_intensity_pct": float(values["led_intensity_pct"]),
        "dark_raw": int(values["dark_raw"]),
        "lit_raw": int(values["lit_raw"]),
        "signal_raw": float(values["signal_raw"]),
        "signal_normalized": float(values["signal_normalized"]),
        "blank_dark_raw": values.get("blank_dark_raw"),
        "blank_lit_raw": values.get("blank_lit_raw"),
        "blank_signal": values.get("blank_signal"),
        "transmission": values.get("transmission"),
        "point_value": values.get("point_value"),
        "included_final": int(bool(values["included_final"])),
        "status": str(values["status"]),
    }


def _result_parser(topic: str, payload: pt.MQTTMessagePayload) -> dict[str, Any]:
    metadata = produce_metadata(topic)
    values = decode(payload)
    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": values["timestamp"],
        "sweep_id": int(values["sweep_id"]),
        "nir_od": values.get("nir_od"),
        "nir_transmission": values.get("nir_transmission"),
        "nir_plateau_min_pct": values.get("nir_plateau_min_pct"),
        "nir_plateau_max_pct": values.get("nir_plateau_max_pct"),
        "nir_selected_intensities": dumps(values.get("nir_selected_intensities", [])),
        "nir_mad": values.get("nir_mad"),
        "nir_status": str(values["nir_status"]),
        "green_signal": values.get("green_signal"),
        "green_reference_intensity_pct": values.get("green_reference_intensity_pct"),
        "green_plateau_min_pct": values.get("green_plateau_min_pct"),
        "green_plateau_max_pct": values.get("green_plateau_max_pct"),
        "green_selected_intensities": dumps(values.get("green_selected_intensities", [])),
        "green_mad": values.get("green_mad"),
        "green_relative_mad": values.get("green_relative_mad"),
        "green_status": str(values["green_status"]),
    }


def _calibration_parser(topic: str, payload: pt.MQTTMessagePayload) -> dict[str, Any]:
    metadata = produce_metadata(topic)
    values = decode(payload)
    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": values["timestamp"],
        "modality": str(values["modality"]),
        "led_channel": str(values["led_channel"]),
        "led_intensity_pct": float(values["led_intensity_pct"]),
        "gain": int(values["gain"]),
        "dark_raw": int(values["dark_raw"]),
        "lit_raw": int(values["lit_raw"]),
        "signal_raw": float(values["signal_raw"]),
    }


register_source_to_sink(
    [
        TopicToParserToTable(f"pioreactor/+/+/{POINT_TOPIC}", _point_parser, "optical_sweep_points"),
        TopicToParserToTable(f"pioreactor/+/+/{RESULT_TOPIC}", _result_parser, "optical_sweep_results"),
        TopicToParserToTable(
            f"pioreactor/+/+/{CALIBRATION_TOPIC}",
            _calibration_parser,
            "optical_sweep_calibrations",
        ),
    ]
)


def _make_sensor() -> Any:
    try:
        return adafruit_as7341.AS7341(board.I2C())
    except Exception as exc:
        raise HardwareNotFoundError("AS7341 not detected on the Pioreactor I2C bus") from exc


def _read_channel(sensor: Any, modality: str, averages: int) -> int:
    values: list[int] = []
    for _ in range(max(1, averages)):
        if modality == "nir":
            values.append(int(sensor.channel_nir))
        elif modality == "green":
            values.append(int(sensor.channel_515nm))
        else:
            raise ValueError(f"unknown modality: {modality}")
    return int(round(mean(values)))


def _led_channels() -> tuple[str, str]:
    nir = config.get(CONFIG_SECTION, "nir_led_channel", fallback="D").upper()
    blue = config.get(CONFIG_SECTION, "blue_led_channel", fallback="C").upper()
    if nir not in led_utils.ALL_LED_CHANNELS or blue not in led_utils.ALL_LED_CHANNELS:
        raise ValueError("nir_led_channel and blue_led_channel must be Pioreactor LED channels A-D")
    if nir == blue:
        raise ValueError("nir_led_channel and blue_led_channel must be different channels")
    return nir, blue


def _gain_for(modality: str) -> int:
    key = "nir_gain" if modality == "nir" else "green_gain"
    return config.getint(CONFIG_SECTION, key, fallback=10)


def _calibration_key(unit: str, modality: str) -> str:
    return f"{unit}:{modality}"


def _load_calibration(unit: str, modality: str) -> dict[str, Any] | None:
    with local_persistent_storage(CALIBRATION_CACHE) as cache:
        raw = cache.get(_calibration_key(unit, modality))
    if raw is None:
        return None
    return loads(raw)


def _calibration_matches(
    calibration: dict[str, Any] | None,
    *,
    modality: str,
    led_channel: str,
    levels: tuple[int, ...],
    gain: int,
) -> bool:
    if calibration is None:
        return False
    return (
        calibration.get("modality") == modality
        and calibration.get("led_channel") == led_channel
        and tuple(calibration.get("levels", [])) == levels
        and int(calibration.get("gain", -1)) == gain
    )


def _measure_one_point(
    sensor: Any,
    *,
    modality: str,
    selected_led_channel: str,
    controlled_channels: tuple[str, str],
    intensity_pct: float,
    settle_time_s: float,
    averages: int,
    unit: str,
    experiment: str,
    source_of_event: str,
    lock_owner: str,
) -> tuple[int, int]:
    off_state = {channel: 0.0 for channel in controlled_channels}
    lit_state = dict(off_state)
    lit_state[selected_led_channel] = float(intensity_pct)

    with led_utils.change_leds_intensities_temporarily(
        off_state,
        unit=unit,
        experiment=experiment,
        source_of_event=source_of_event,
        verbose=False,
        lock_owner=lock_owner,
    ):
        sleep(settle_time_s)
        dark_raw = _read_channel(sensor, modality, averages)

        with led_utils.change_leds_intensities_temporarily(
            lit_state,
            unit=unit,
            experiment=experiment,
            source_of_event=source_of_event,
            verbose=False,
            lock_owner=lock_owner,
        ):
            sleep(settle_time_s)
            lit_raw = _read_channel(sensor, modality, averages)

    return dark_raw, lit_raw


def _point_base_valid(
    *,
    signal: float,
    lit_raw: int,
    blank_signal: float | None,
    blank_lit_raw: int | None,
    minimum_signal_raw: float,
    saturation_raw: int,
    modality: str,
) -> tuple[bool, str]:
    if lit_raw >= saturation_raw:
        return False, "sample_saturated"
    if signal <= minimum_signal_raw:
        return False, "sample_signal_too_low"
    if blank_signal is None or blank_lit_raw is None:
        return False, "no_calibration"
    if blank_lit_raw >= saturation_raw:
        return False, "blank_saturated"
    if modality == "nir" and blank_signal <= minimum_signal_raw:
        return False, "blank_signal_too_low"
    return True, "candidate"


def _plateau_kwargs() -> dict[str, Any]:
    return {
        "min_points": config.getint(CONFIG_SECTION, "plateau_min_points", fallback=4),
        "r2_min": config.getfloat(CONFIG_SECTION, "plateau_r2_min", fallback=0.995),
        "slope_cv_max": config.getfloat(CONFIG_SECTION, "plateau_slope_cv_max", fallback=0.10),
    }


def _selected_levels(levels: tuple[int, ...], metric: FinalMetric) -> list[int]:
    return [levels[index] for index in metric.selected_indices]


def _plateau_bounds(levels: tuple[int, ...], metric: FinalMetric) -> tuple[int | None, int | None]:
    if not metric.plateau_indices:
        return None, None
    return levels[metric.plateau_indices[0]], levels[metric.plateau_indices[-1]]


class OpticalSweepReading(BackgroundJobContrib):
    job_name = JOB_NAME

    published_settings = {
        "nir_od": {"datatype": "float", "unit": "AU", "settable": False},
        "nir_transmission": {"datatype": "float", "unit": "%", "settable": False},
        "green_signal": {"datatype": "float", "unit": "AU", "settable": False},
        "nir_status": {"datatype": "string", "settable": False},
        "green_status": {"datatype": "string", "settable": False},
        "sweep_id": {"datatype": "integer", "settable": False},
        "last_sweep_timestamp": {"datatype": "string", "settable": False},
    }

    def __init__(self, unit: str, experiment: str) -> None:
        super().__init__(unit=unit, experiment=experiment, plugin_name=PLUGIN_NAME)
        self.sensor = _make_sensor()
        self.levels = _configured_levels()
        self.nir_led_channel, self.blue_led_channel = _led_channels()
        self.settle_time_s = config.getfloat(CONFIG_SECTION, "settle_time_s", fallback=0.10)
        self.averages = config.getint(CONFIG_SECTION, "averages_per_point", fallback=1)
        self.minimum_signal_raw = config.getfloat(CONFIG_SECTION, "minimum_signal_raw", fallback=25.0)
        self.minimum_green_net_raw = config.getfloat(CONFIG_SECTION, "minimum_green_net_raw", fallback=10.0)
        self.saturation_raw = config.getint(CONFIG_SECTION, "saturation_raw", fallback=65000)
        self.reference_green_pct = config.getfloat(
            CONFIG_SECTION, "green_reference_intensity_pct", fallback=50.0
        )

        self.nir_od = 0.0
        self.nir_transmission = 0.0
        self.green_signal = 0.0
        self.nir_status = "waiting"
        self.green_status = "waiting"
        self.sweep_id = 0
        self.last_sweep_timestamp = ""
        self._cycle_lock = Lock()
        self._timer: RepeatedTimer | None = None

        interval_s = config.getfloat(CONFIG_SECTION, "sweep_interval_s", fallback=60.0)
        if interval_s <= 0:
            self.clean_up()
            raise ValueError("sweep_interval_s must be > 0")

        self._timer = RepeatedTimer(
            interval_s,
            self._run_cycle,
            job_name=self.job_name,
            run_immediately=True,
            logger=self.logger,
        ).start()

    def on_disconnected(self) -> None:
        with suppress(AttributeError):
            if self._timer is not None:
                self._timer.cancel()
        super().on_disconnected()

    def _acquire_modality(self, modality: str, lock_owner: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        led_channel = self.nir_led_channel if modality == "nir" else self.blue_led_channel
        gain = _gain_for(modality)
        self.sensor.gain = gain
        calibration = _load_calibration(self.unit, modality)
        if not _calibration_matches(
            calibration,
            modality=modality,
            led_channel=led_channel,
            levels=self.levels,
            gain=gain,
        ):
            calibration = None

        points: list[dict[str, Any]] = []
        for point_index, intensity in enumerate(self.levels):
            dark_raw, lit_raw = _measure_one_point(
                self.sensor,
                modality=modality,
                selected_led_channel=led_channel,
                controlled_channels=(self.nir_led_channel, self.blue_led_channel),
                intensity_pct=float(intensity),
                settle_time_s=self.settle_time_s,
                averages=self.averages,
                unit=self.unit,
                experiment=self.experiment,
                source_of_event=self.job_name,
                lock_owner=lock_owner,
            )
            signal = float(lit_raw - dark_raw)
            blank_point = calibration["points"].get(str(intensity)) if calibration else None
            blank_signal = float(blank_point["signal_raw"]) if blank_point is not None else None
            blank_dark_raw = int(blank_point["dark_raw"]) if blank_point is not None else None
            blank_lit_raw = int(blank_point["lit_raw"]) if blank_point is not None else None
            valid, status = _point_base_valid(
                signal=signal,
                lit_raw=lit_raw,
                blank_signal=blank_signal,
                blank_lit_raw=blank_lit_raw,
                minimum_signal_raw=self.minimum_signal_raw,
                saturation_raw=self.saturation_raw,
                modality=modality,
            )
            if modality == "green" and blank_signal is not None and signal - blank_signal <= self.minimum_green_net_raw:
                valid, status = False, "green_net_signal_too_low"

            points.append(
                {
                    "timestamp": current_utc_datetime().isoformat(),
                    "sweep_id": self.sweep_id,
                    "modality": modality,
                    "point_index": point_index,
                    "led_channel": led_channel,
                    "led_intensity_pct": float(intensity),
                    "dark_raw": dark_raw,
                    "lit_raw": lit_raw,
                    "signal_raw": signal,
                    "signal_normalized": _gain_time_normalize(self.sensor, max(signal, 0.0)),
                    "blank_dark_raw": blank_dark_raw,
                    "blank_lit_raw": blank_lit_raw,
                    "blank_signal": blank_signal,
                    "base_valid": valid,
                    "status": status,
                    "transmission": None,
                    "point_value": None,
                    "included_final": False,
                }
            )
        return points, calibration

    def _evaluate_nir(self, points: list[dict[str, Any]], calibration: dict[str, Any] | None) -> FinalMetric:
        if calibration is None:
            return FinalMetric(None, None, tuple(), tuple(), None, None, "no_calibration")
        sample = [float(point["signal_raw"]) for point in points]
        blank = [float(point["blank_signal"]) for point in points]
        valid = [bool(point["base_valid"]) for point in points]
        plateau = find_linear_plateau(
            self.levels,
            sample,
            valid,
            secondary=blank,
            **_plateau_kwargs(),
        )
        metric = finalize_nir(
            sample,
            blank,
            plateau,
            max_od_mad=config.getfloat(CONFIG_SECTION, "nir_max_od_mad", fallback=0.03),
            max_transmission=config.getfloat(CONFIG_SECTION, "nir_max_transmission", fallback=1.20),
        )
        for index, point in enumerate(points):
            blank_signal = float(point["blank_signal"])
            if blank_signal > 0 and float(point["signal_raw"]) > 0:
                transmission = float(point["signal_raw"]) / blank_signal
                point["transmission"] = transmission
                if isfinite(transmission) and transmission > 0:
                    from math import log10
                    point["point_value"] = -log10(transmission)
            if index in metric.plateau_indices and point["base_valid"]:
                point["status"] = "plateau"
            if index in metric.selected_indices:
                point["included_final"] = metric.status == "ok"
                point["status"] = "selected" if metric.status == "ok" else metric.status
        return metric

    def _evaluate_green(self, points: list[dict[str, Any]], calibration: dict[str, Any] | None) -> FinalMetric:
        if calibration is None:
            return FinalMetric(None, None, tuple(), tuple(), None, None, "no_calibration")
        sample = [float(point["signal_raw"]) for point in points]
        blank = [float(point["blank_signal"]) for point in points]
        net = [sample_value - blank_value for sample_value, blank_value in zip(sample, blank)]
        valid = [bool(point["base_valid"]) for point in points]
        plateau = find_linear_plateau(self.levels, net, valid, **_plateau_kwargs())
        metric = finalize_green(
            self.levels,
            sample,
            blank,
            plateau,
            reference_intensity_pct=self.reference_green_pct,
            max_relative_mad=config.getfloat(CONFIG_SECTION, "green_max_relative_mad", fallback=0.10),
        )
        for index, point in enumerate(points):
            intensity = float(point["led_intensity_pct"])
            net_signal = float(point["signal_raw"]) - float(point["blank_signal"])
            if intensity > 0 and net_signal > 0:
                point["point_value"] = net_signal * self.reference_green_pct / intensity
            if index in metric.plateau_indices and point["base_valid"]:
                point["status"] = "plateau"
            if index in metric.selected_indices:
                point["included_final"] = metric.status == "ok"
                point["status"] = "selected" if metric.status == "ok" else metric.status
        return metric

    def _publish_points(self, points: list[dict[str, Any]]) -> None:
        topic = f"pioreactor/{self.unit}/{self.experiment}/{POINT_TOPIC}"
        for point in points:
            point.pop("base_valid", None)
            self.publish(topic, point)

    def _run_cycle(self) -> None:
        if not self._cycle_lock.acquire(blocking=False):
            self.logger.warning("Previous optical sweep is still running; skipping this scheduled cycle.")
            return
        try:
            with led_utils.lock_leds_temporarily(
                [self.nir_led_channel, self.blue_led_channel]
            ) as lock_owner:
                nir_points, nir_calibration = self._acquire_modality("nir", lock_owner)
                nir_metric = self._evaluate_nir(nir_points, nir_calibration)
                self._publish_points(nir_points)

                green_points, green_calibration = self._acquire_modality("green", lock_owner)
                green_metric = self._evaluate_green(green_points, green_calibration)
                self._publish_points(green_points)

            timestamp = current_utc_datetime().isoformat()
            nir_min, nir_max = _plateau_bounds(self.levels, nir_metric)
            green_min, green_max = _plateau_bounds(self.levels, green_metric)
            result = {
                "timestamp": timestamp,
                "sweep_id": self.sweep_id,
                "nir_od": nir_metric.value,
                "nir_transmission": nir_metric.auxiliary,
                "nir_plateau_min_pct": nir_min,
                "nir_plateau_max_pct": nir_max,
                "nir_selected_intensities": _selected_levels(self.levels, nir_metric),
                "nir_mad": nir_metric.mad,
                "nir_status": nir_metric.status,
                "green_signal": green_metric.value,
                "green_reference_intensity_pct": self.reference_green_pct,
                "green_plateau_min_pct": green_min,
                "green_plateau_max_pct": green_max,
                "green_selected_intensities": _selected_levels(self.levels, green_metric),
                "green_mad": green_metric.mad,
                "green_relative_mad": green_metric.relative_mad,
                "green_status": green_metric.status,
            }
            self.publish(f"pioreactor/{self.unit}/{self.experiment}/{RESULT_TOPIC}", result)

            self.nir_status = nir_metric.status
            self.green_status = green_metric.status
            if nir_metric.status == "ok" and nir_metric.value is not None:
                self.nir_od = nir_metric.value
                self.nir_transmission = 100.0 * float(nir_metric.auxiliary or 0.0)
            if green_metric.status == "ok" and green_metric.value is not None:
                self.green_signal = green_metric.value
            self.last_sweep_timestamp = timestamp
            self.sweep_id += 1
        except Exception:
            self.logger.error("Optical sweep failed.", exc_info=True)
            self.nir_status = "error"
            self.green_status = "error"
        finally:
            self._cycle_lock.release()


@click.command(name=JOB_NAME)
def click_optical_sweep_reading() -> None:
    """Run sequential NIR and blue-excited 515 nm sweeps."""
    unit = get_unit_name()
    experiment = get_assigned_experiment_name(unit)
    with OpticalSweepReading(unit=unit, experiment=experiment) as job:
        job.block_until_disconnected()


def _capture_calibration_modality(
    *,
    sensor: Any,
    modality: str,
    unit: str,
    experiment: str,
    levels: tuple[int, ...],
    nir_led_channel: str,
    blue_led_channel: str,
    settle_time_s: float,
    averages: int,
    lock_owner: str,
) -> dict[str, Any]:
    led_channel = nir_led_channel if modality == "nir" else blue_led_channel
    gain = _gain_for(modality)
    sensor.gain = gain
    points: dict[str, dict[str, Any]] = {}
    topic = f"pioreactor/{unit}/{experiment}/{CALIBRATION_TOPIC}"
    for intensity in levels:
        dark_raw, lit_raw = _measure_one_point(
            sensor,
            modality=modality,
            selected_led_channel=led_channel,
            controlled_channels=(nir_led_channel, blue_led_channel),
            intensity_pct=float(intensity),
            settle_time_s=settle_time_s,
            averages=averages,
            unit=unit,
            experiment=experiment,
            source_of_event=CALIBRATION_JOB_NAME,
            lock_owner=lock_owner,
        )
        signal = float(lit_raw - dark_raw)
        if modality == "nir" and signal <= 0:
            raise click.ClickException(
                f"NIR blank signal at {intensity}% is <= 0 ({signal}). Check wiring and optical alignment."
            )
        point = {"dark_raw": dark_raw, "lit_raw": lit_raw, "signal_raw": signal}
        points[str(intensity)] = point
        pubsub.publish(
            topic,
            encode(
                {
                    "timestamp": current_utc_datetime().isoformat(),
                    "modality": modality,
                    "led_channel": led_channel,
                    "led_intensity_pct": float(intensity),
                    "gain": gain,
                    **point,
                }
            ),
        )
    calibration = {
        "modality": modality,
        "timestamp": current_utc_datetime().isoformat(),
        "led_channel": led_channel,
        "gain": gain,
        "levels": list(levels),
        "points": points,
    }
    with local_persistent_storage(CALIBRATION_CACHE) as cache:
        cache[_calibration_key(unit, modality)] = dumps(calibration)
    return calibration


@click.command(name=CALIBRATION_JOB_NAME)
@click.option(
    "--mode",
    type=click.Choice(["both", "nir", "green"], case_sensitive=False),
    default="both",
    show_default=True,
)
@click.option("--samples", default=None, type=int, help="Averages per dark/lit measurement.")
def click_optical_sweep_calibration(mode: str, samples: int | None) -> None:
    """Capture blank-medium calibration sweeps for NIR and/or green fluorescence."""
    unit = get_unit_name()
    experiment = get_assigned_experiment_name(unit)
    logger = create_logger(CALIBRATION_JOB_NAME, unit=unit, experiment=experiment)
    if is_pio_job_running(JOB_NAME):
        raise click.ClickException(f"Stop {JOB_NAME} before calibration.")

    levels = _configured_levels()
    nir_led_channel, blue_led_channel = _led_channels()
    settle_time_s = config.getfloat(CONFIG_SECTION, "settle_time_s", fallback=0.10)
    averages = samples or config.getint(CONFIG_SECTION, "calibration_averages_per_point", fallback=3)
    if averages <= 0:
        raise click.ClickException("samples must be > 0")

    sensor = _make_sensor()
    modalities = ("nir", "green") if mode.lower() == "both" else (mode.lower(),)
    with managed_lifecycle(unit, experiment, CALIBRATION_JOB_NAME):
        with led_utils.lock_leds_temporarily([nir_led_channel, blue_led_channel]) as lock_owner:
            for modality in modalities:
                logger.info(f"Capturing {modality} blank sweep at levels {levels}.")
                _capture_calibration_modality(
                    sensor=sensor,
                    modality=modality,
                    unit=unit,
                    experiment=experiment,
                    levels=levels,
                    nir_led_channel=nir_led_channel,
                    blue_led_channel=blue_led_channel,
                    settle_time_s=settle_time_s,
                    averages=averages,
                    lock_owner=lock_owner,
                )
                logger.info(f"Stored {modality} blank calibration.")

    pubsub.publish(
        f"pioreactor/{unit}/{experiment}/{CALIBRATION_JOB_NAME}/status",
        f"calibrated:{','.join(modalities)}",
        retain=True,
    )
    click.echo("Optical sweep calibration stored successfully.")
