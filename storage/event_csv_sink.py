#!/usr/bin/env python3
import csv
from datetime import datetime, timezone
from pathlib import Path


EVENT_CSV_HEADERS = (
    "received_at",
    "packet_timestamp",
    "msg_type",
    "event_type",
    "device_id",
    "seq",
    "voltage_mv",
    "current_ma",
    "power_w",
    "charge_state",
    "alarm",
)


def write_event_csv(csv_path, event):
    csv_path = Path(csv_path)
    file_exists = csv_path.exists()

    with csv_path.open("a", newline="") as csv_file:
        writer = csv.writer(csv_file)
        if not file_exists or csv_path.stat().st_size == 0:
            writer.writerow(EVENT_CSV_HEADERS)

        fields = event.get("fields", {})
        packet_timestamp = datetime.fromtimestamp(event["timestamp"], tz=timezone.utc).isoformat()
        writer.writerow(
            [
                datetime.now(timezone.utc).isoformat(),
                packet_timestamp,
                event["msg_type"].name,
                event["event_type"].name,
                event["device_id"],
                event["seq"],
                fields.get("voltage_mv"),
                fields.get("current_ma"),
                fields.get("power_w"),
                fields.get("charge_state"),
                fields.get("alarm"),
            ]
        )
        csv_file.flush()
