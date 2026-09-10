CREATE TABLE IF NOT EXISTS nir_spectrometer_readings (
    experiment              TEXT NOT NULL,
    pioreactor_unit         TEXT NOT NULL,
    timestamp               TEXT NOT NULL,
    sweep_id                INTEGER NOT NULL,
    point_index             INTEGER NOT NULL,
    led_channel             TEXT NOT NULL,
    led_intensity_pct       REAL NOT NULL,
    dark_raw                INTEGER,
    lit_raw                 INTEGER,
    signal_raw              REAL,
    signal_normalized       REAL,
    blank_signal            REAL,
    transmission            REAL,
    nir_od                  REAL,
    valid                   INTEGER,
    status                  TEXT
);

CREATE INDEX IF NOT EXISTS idx_nir_spectrometer_readings_exp_time
ON nir_spectrometer_readings (experiment, pioreactor_unit, timestamp);

CREATE VIEW IF NOT EXISTS nir_spectrometer_valid_readings AS
SELECT * FROM nir_spectrometer_readings WHERE valid = 1;
