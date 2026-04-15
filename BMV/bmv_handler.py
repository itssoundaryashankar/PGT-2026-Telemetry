#!/usr/bin/env python3
from datetime import datetime, timezone


def format_bmv_packet(decoded):
    timestamp = datetime.fromtimestamp(decoded["timestamp"], tz=timezone.utc).isoformat()
    return (
        f"[rx:bmv] event={decoded['event_type'].name} "
        f"device={decoded['device_id']} "
        f"seq={decoded['seq']} "
        f"timestamp={timestamp} "
        f"fields={decoded['fields']}"
    )
