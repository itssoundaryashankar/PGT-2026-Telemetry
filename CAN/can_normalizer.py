#!/usr/bin/env python3
"""Normalizers for CAN-sourced telemetry. Each one takes the raw fields
emitted by a CAN reader snapshot plus a device_id, and returns the
standard reading dict shape used throughout the telemetry pipeline:

    {
        "device_type": "mppt" | "bms",
        "device_id": int,
        "timestamp": int,
        "fields": {<flat name>: <numeric value>, ...}
    }
"""

import time


def _coerce_numeric(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize(device_type, device_id, frame):
    fields = {}
    for k, v in (frame or {}).items():
        coerced = _coerce_numeric(v)
        if coerced is not None:
            fields[k] = coerced
    return {
        "device_type": device_type,
        "device_id": device_id,
        "timestamp": int(time.time()),
        "fields": fields,
    }


def normalize_mppt_frame(frame, device_id):
    return _normalize("mppt", device_id, frame)


def normalize_bms_frame(frame, device_id):
    return _normalize("bms", device_id, frame)
