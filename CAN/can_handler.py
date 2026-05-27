#!/usr/bin/env python3
"""Formatter for decoded CAN packets, mirroring bmv_handler.format_bmv_packet.

Returns a human-readable single line that the receiver prints. The actual
InfluxDB write happens in the sink chain (InfluxWriter), not here — this
is purely for console visibility.
"""

from datetime import datetime, timezone


def format_can_packet(decoded):
    timestamp = datetime.fromtimestamp(decoded["timestamp"], tz=timezone.utc).isoformat()
    fields = decoded["fields"]
    return (
        f"[rx:can:{decoded['msg_type'].name}] "
        f"event={decoded['event_type'].name} "
        f"device={decoded['device_id']} "
        f"seq={decoded['seq']} "
        f"timestamp={timestamp} "
        f"can_id={fields.get('can_id')} "
        f"ext={fields.get('extended')} "
        f"dlc={fields.get('dlc')} "
        f"data={fields.get('data_hex')}"
    )
