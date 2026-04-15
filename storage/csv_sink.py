#!/usr/bin/env python3
import csv
from datetime import datetime
from pathlib import Path


CSV_HEADERS = (
    "timestamp",
    "voltage_mv",
    "current_ma",
    "power_w",
    "charge_state",
    "alarm",
)


def write_telemetry_csv(csv_path, reading):
    csv_path = Path(csv_path)
    file_exists = csv_path.exists()

    with csv_path.open("a", newline="") as csv_file:
        writer = csv.writer(csv_file)
        if not file_exists or csv_path.stat().st_size == 0:
            writer.writerow(CSV_HEADERS)

        fields = reading["fields"]
        writer.writerow(
            [
                datetime.utcnow().isoformat(),
                fields.get("voltage_mv"),
                fields.get("current_ma"),
                fields.get("power_w"),
                fields.get("charge_state"),
                fields.get("alarm"),
            ]
        )
        csv_file.flush()
