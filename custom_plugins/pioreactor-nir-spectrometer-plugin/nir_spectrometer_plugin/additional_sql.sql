CREATE TABLE IF NOT EXISTS optical_sweep_points (
    experiment              TEXT NOT NULL,
    pioreactor_unit         TEXT NOT NULL,
    timestamp               TEXT NOT NULL,
    sweep_id                INTEGER NOT NULL,
    modality                TEXT NOT NULL,
    point_index             INTEGER NOT NULL,
    led_channel             TEXT NOT NULL,
    led_intensity_pct       REAL NOT NULL,
    dark_raw                INTEGER,
    lit_raw                 INTEGER,
    signal_raw              REAL,
    signal_normalized       REAL,
    blank_dark_raw          INTEGER,
    blank_lit_raw           INTEGER,
    blank_signal            REAL,
    transmission            REAL,
    point_value             REAL,
    included_final          INTEGER,
    status                  TEXT
);

CREATE INDEX IF NOT EXISTS idx_optical_sweep_points_exp_time
ON optical_sweep_points (experiment, pioreactor_unit, timestamp);

CREATE INDEX IF NOT EXISTS idx_optical_sweep_points_sweep
ON optical_sweep_points (experiment, pioreactor_unit, sweep_id, modality, point_index);

CREATE TABLE IF NOT EXISTS optical_sweep_results (
    experiment                      TEXT NOT NULL,
    pioreactor_unit                 TEXT NOT NULL,
    timestamp                       TEXT NOT NULL,
    sweep_id                        INTEGER NOT NULL,
    nir_od                          REAL,
    nir_transmission                REAL,
    nir_plateau_min_pct             REAL,
    nir_plateau_max_pct             REAL,
    nir_selected_intensities        TEXT,
    nir_mad                         REAL,
    nir_status                      TEXT,
    green_signal                    REAL,
    green_reference_intensity_pct   REAL,
    green_plateau_min_pct           REAL,
    green_plateau_max_pct           REAL,
    green_selected_intensities      TEXT,
    green_mad                       REAL,
    green_relative_mad              REAL,
    green_status                    TEXT
);

CREATE INDEX IF NOT EXISTS idx_optical_sweep_results_exp_time
ON optical_sweep_results (experiment, pioreactor_unit, timestamp);

CREATE TABLE IF NOT EXISTS optical_sweep_calibrations (
    experiment              TEXT NOT NULL,
    pioreactor_unit         TEXT NOT NULL,
    timestamp               TEXT NOT NULL,
    modality                TEXT NOT NULL,
    led_channel             TEXT NOT NULL,
    led_intensity_pct       REAL NOT NULL,
    gain                    INTEGER NOT NULL,
    dark_raw                INTEGER,
    lit_raw                 INTEGER,
    signal_raw              REAL
);

CREATE INDEX IF NOT EXISTS idx_optical_sweep_calibrations_exp_time
ON optical_sweep_calibrations (experiment, pioreactor_unit, timestamp, modality);
