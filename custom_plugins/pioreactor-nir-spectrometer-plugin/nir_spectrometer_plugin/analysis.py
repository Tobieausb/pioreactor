from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from math import log10
from statistics import median
from statistics import pstdev
from typing import Sequence


@dataclass(frozen=True)
class PlateauSelection:
    indices: tuple[int, ...]
    selected_indices: tuple[int, ...]
    primary_r2: float
    secondary_r2: float | None
    primary_slope_cv: float
    secondary_slope_cv: float | None


@dataclass(frozen=True)
class FinalMetric:
    value: float | None
    auxiliary: float | None
    selected_indices: tuple[int, ...]
    plateau_indices: tuple[int, ...]
    mad: float | None
    relative_mad: float | None
    status: str


def _linear_fit(x: Sequence[float], y: Sequence[float]) -> tuple[float, float, float]:
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("linear fit needs equal-length vectors with at least two points")
    xbar = sum(x) / len(x)
    ybar = sum(y) / len(y)
    ss_xx = sum((xi - xbar) ** 2 for xi in x)
    if ss_xx <= 0:
        raise ValueError("x values must not all be identical")
    slope = sum((xi - xbar) * (yi - ybar) for xi, yi in zip(x, y)) / ss_xx
    intercept = ybar - slope * xbar
    fitted = [slope * xi + intercept for xi in x]
    ss_res = sum((yi - fi) ** 2 for yi, fi in zip(y, fitted))
    ss_tot = sum((yi - ybar) ** 2 for yi in y)
    r2 = 1.0 if ss_tot == 0 and ss_res == 0 else (0.0 if ss_tot == 0 else 1.0 - ss_res / ss_tot)
    return slope, intercept, r2


def _slope_cv(x: Sequence[float], y: Sequence[float]) -> float:
    slopes = []
    for x0, x1, y0, y1 in zip(x[:-1], x[1:], y[:-1], y[1:]):
        dx = x1 - x0
        if dx <= 0:
            return float("inf")
        slopes.append((y1 - y0) / dx)
    if not slopes:
        return float("inf")
    mean_slope = sum(slopes) / len(slopes)
    if mean_slope <= 0:
        return float("inf")
    return pstdev(slopes) / abs(mean_slope) if len(slopes) > 1 else 0.0


def _contiguous_runs(valid: Sequence[bool]) -> list[tuple[int, ...]]:
    runs: list[tuple[int, ...]] = []
    current: list[int] = []
    for index, ok in enumerate(valid):
        if ok:
            current.append(index)
        elif current:
            runs.append(tuple(current))
            current = []
    if current:
        runs.append(tuple(current))
    return runs


def _three_central(indices: Sequence[int], levels: Sequence[float]) -> tuple[int, ...]:
    if len(indices) < 3:
        return tuple()
    center = (levels[indices[0]] + levels[indices[-1]]) / 2.0
    nearest = sorted(indices, key=lambda i: (abs(levels[i] - center), levels[i]))[:3]
    return tuple(sorted(nearest))


def find_linear_plateau(
    levels: Sequence[float],
    primary: Sequence[float],
    valid: Sequence[bool],
    *,
    secondary: Sequence[float] | None = None,
    min_points: int = 4,
    r2_min: float = 0.995,
    slope_cv_max: float = 0.10,
) -> PlateauSelection | None:
    if not (len(levels) == len(primary) == len(valid)):
        raise ValueError("levels, primary and valid must have equal length")
    if secondary is not None and len(secondary) != len(levels):
        raise ValueError("secondary must match levels length")
    if min_points < 3:
        raise ValueError("min_points must be >= 3")

    candidates: list[tuple[tuple[float, ...], PlateauSelection]] = []
    for run in _contiguous_runs(valid):
        if len(run) < min_points:
            continue
        for start in range(0, len(run) - min_points + 1):
            for stop in range(start + min_points, len(run) + 1):
                indices = run[start:stop]
                x = [float(levels[i]) for i in indices]
                y = [float(primary[i]) for i in indices]
                slope, _, r2 = _linear_fit(x, y)
                cv = _slope_cv(x, y)
                if slope <= 0 or r2 < r2_min or cv > slope_cv_max:
                    continue

                secondary_r2: float | None = None
                secondary_cv: float | None = None
                if secondary is not None:
                    y2 = [float(secondary[i]) for i in indices]
                    slope2, _, secondary_r2 = _linear_fit(x, y2)
                    secondary_cv = _slope_cv(x, y2)
                    if slope2 <= 0 or secondary_r2 < r2_min or secondary_cv > slope_cv_max:
                        continue

                selected = _three_central(indices, levels)
                if len(selected) != 3:
                    continue

                selection = PlateauSelection(
                    indices=tuple(indices),
                    selected_indices=selected,
                    primary_r2=r2,
                    secondary_r2=secondary_r2,
                    primary_slope_cv=cv,
                    secondary_slope_cv=secondary_cv,
                )
                combined_cv = cv + (secondary_cv or 0.0)
                combined_r2 = r2 + (secondary_r2 or 0.0)
                score = (-float(len(indices)), combined_cv, -combined_r2)
                candidates.append((score, selection))

    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _mad(values: Sequence[float]) -> float:
    center = median(values)
    return float(median([abs(value - center) for value in values]))


def finalize_nir(
    sample_signal: Sequence[float],
    blank_signal: Sequence[float],
    plateau: PlateauSelection | None,
    *,
    max_od_mad: float = 0.03,
    max_transmission: float = 1.20,
) -> FinalMetric:
    if plateau is None:
        return FinalMetric(None, None, tuple(), tuple(), None, None, "no_linear_plateau")

    ods: list[float] = []
    transmissions: list[float] = []
    for index in plateau.selected_indices:
        blank = float(blank_signal[index])
        sample = float(sample_signal[index])
        if blank <= 0 or sample <= 0:
            return FinalMetric(None, None, plateau.selected_indices, plateau.indices, None, None, "invalid_signal")
        transmission = sample / blank
        if not isfinite(transmission) or transmission <= 0 or transmission > max_transmission:
            return FinalMetric(None, None, plateau.selected_indices, plateau.indices, None, None, "invalid_transmission")
        transmissions.append(transmission)
        ods.append(-log10(transmission))

    final_od = float(median(ods))
    final_transmission = float(median(transmissions))
    mad = _mad(ods)
    relative_mad = mad / max(abs(final_od), 1e-12)
    if mad > max_od_mad:
        return FinalMetric(None, final_transmission, plateau.selected_indices, plateau.indices, mad, relative_mad, "high_point_spread")
    return FinalMetric(final_od, final_transmission, plateau.selected_indices, plateau.indices, mad, relative_mad, "ok")


def finalize_green(
    levels: Sequence[float],
    sample_signal: Sequence[float],
    blank_signal: Sequence[float],
    plateau: PlateauSelection | None,
    *,
    reference_intensity_pct: float = 50.0,
    max_relative_mad: float = 0.10,
) -> FinalMetric:
    if plateau is None:
        return FinalMetric(None, None, tuple(), tuple(), None, None, "no_linear_plateau")

    equivalents: list[float] = []
    for index in plateau.selected_indices:
        intensity = float(levels[index])
        net = float(sample_signal[index]) - float(blank_signal[index])
        if intensity <= 0 or net <= 0 or not isfinite(net):
            return FinalMetric(None, None, plateau.selected_indices, plateau.indices, None, None, "invalid_green_signal")
        equivalents.append(net * reference_intensity_pct / intensity)

    final_signal = float(median(equivalents))
    mad = _mad(equivalents)
    relative_mad = mad / max(abs(final_signal), 1e-12)
    if relative_mad > max_relative_mad:
        return FinalMetric(None, None, plateau.selected_indices, plateau.indices, mad, relative_mad, "high_point_spread")
    return FinalMetric(final_signal, None, plateau.selected_indices, plateau.indices, mad, relative_mad, "ok")
