#!/usr/bin/env python3
"""Normalize a raw CAN frame into the telemetry-pipeline's standard shape.

The receiver flattens `fields` into dotted keys, so we keep the field set
small and InfluxDB-friendly: a hex string for the CAN payload plus the
DLC. The CAN ID is carried at the top level so the policy and packet
builder can look at it without digging into fields.

Routing into the "MPPT" vs "BMS" msg_type buckets happens here too — you
configure which CAN ID belongs to which subsystem via `id_to_msg_type`,
and that decides which Influx bucket the receiver will land it in.
"""

import time


# Default msg_type for any CAN ID not present in id_to_msg_type.
DEFAULT_CAN_MSG_TYPE = "MPPT"


def make_normalizer(id_to_msg_type=None, device_id_lookup=None,
                    default_msg_type=DEFAULT_CAN_MSG_TYPE):
    """Build a normalize_can_frame(frame, device_id) closure.

    id_to_msg_type:    {can_id: "MPPT" | "BMS" | ...}  — picks the Influx
                       bucket via the receiver's bucket_map.
    device_id_lookup:  {can_id: int} — optional per-ID device_id override,
                       in case you want CAN sub-devices to show up as
                       distinct device_ids in Influx. If a CAN ID isn't
                       in the lookup, the device_id passed in is used
                       as-is.
    default_msg_type:  fallback when an ID isn't in id_to_msg_type.
    """
    id_to_msg_type = dict(id_to_msg_type or {})
    device_id_lookup = dict(device_id_lookup or {})

    def normalize_can_frame(frame, device_id):
        can_id = frame["can_id"]
        data = frame.get("data", b"")
        dlc = frame.get("dlc", len(data))

        msg_type = id_to_msg_type.get(can_id, default_msg_type)
        per_id_device = device_id_lookup.get(can_id, device_id)

        return {
            "device_type": "can",
            "device_id": per_id_device,
            "timestamp": int(frame.get("rx_timestamp") or time.time()),
            # Top-level extras the policy & packet builder care about,
            # but the BMV pipeline never used:
            "msg_type_name": msg_type,
            "can_id": can_id,
            "extended": bool(frame.get("extended", can_id > 0x7FF)),
            "fields": {
                "data_hex": data.hex(),
                "dlc": dlc,
            },
        }

    return normalize_can_frame
