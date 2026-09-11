#!/bin/bash
set -e

export LC_ALL=C

pio log -m "Installed NIR spectrometer plugin; refreshing MQTT-to-DB streaming" -l info || true
sudo systemctl restart pioreactor_startup_run@mqtt_to_db_streaming.service || true
