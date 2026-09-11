from __future__ import annotations

from nir_spectrometer_plugin.analysis import finalize_green
from nir_spectrometer_plugin.analysis import finalize_nir
from nir_spectrometer_plugin.analysis import find_linear_plateau


def test_finds_central_linear_nir_plateau_and_od() -> None:
    levels = list(range(10, 101, 10))
    blank = [100, 900, 1800, 2700, 3600, 4500, 5400, 6100, 6500, 6520]
    sample = [40, 300, 600, 900, 1200, 1500, 1800, 2030, 2160, 2170]
    valid = [b < 6400 and s < 6400 and b > 100 and s > 100 for b, s in zip(blank, sample)]

    plateau = find_linear_plateau(
        levels,
        sample,
        valid,
        secondary=blank,
        min_points=4,
        r2_min=0.99,
        slope_cv_max=0.15,
    )
    result = finalize_nir(sample, blank, plateau)

    assert plateau is not None
    assert len(plateau.selected_indices) == 3
    assert result.status == "ok"
    assert result.value is not None
    assert abs(result.value - 0.4771212547) < 1e-6


def test_green_reports_reference_excitation_equivalent() -> None:
    levels = list(range(10, 101, 10))
    blank = [10 * i for i in range(1, 11)]
    sample = [blank_value + 20 * level for blank_value, level in zip(blank, levels)]
    valid = [True] * len(levels)
    net = [s - b for s, b in zip(sample, blank)]

    plateau = find_linear_plateau(
        levels,
        net,
        valid,
        min_points=4,
        r2_min=0.999,
        slope_cv_max=0.01,
    )
    result = finalize_green(
        levels,
        sample,
        blank,
        plateau,
        reference_intensity_pct=50.0,
        max_relative_mad=0.01,
    )

    assert result.status == "ok"
    assert result.value == 1000.0


def test_no_plateau_rejects_final_value() -> None:
    levels = [10, 20, 30, 40, 50, 60]
    signal = [100, 500, 520, 1200, 1210, 2500]
    plateau = find_linear_plateau(
        levels,
        signal,
        [True] * len(levels),
        min_points=4,
        r2_min=0.999,
        slope_cv_max=0.05,
    )
    assert plateau is None
