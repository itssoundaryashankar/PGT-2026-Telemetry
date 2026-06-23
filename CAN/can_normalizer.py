#!/usr/bin/env python3
"""Decode raw CAN frames into named engineering units.

TPEE Open-SEC CAN protocol — authoritative source: OpenSEC Manual V1.9

CAN ID formula:  can_id = (effective_device_id << 4) | packet_id
where effective_device_id = Reboost tool device ID + physical encoder value

────────────────────────────────────────────────────────────────────
CONFIGURATION — update this list when adding/changing MPPTs
────────────────────────────────────────────────────────────────────
Each entry is the *effective* device ID = Reboost ID + encoder value.
To find it: take the CAN ID from candump, shift right 4 bits.
e.g. candump shows 0x020 -> 0x020 >> 4 = 2
     candump shows 0x100 -> 0x100 >> 4 = 16

The order of this list determines mppt_index (0, 1, 2 ...) and
therefore InfluxDB device_id (base + index).
────────────────────────────────────────────────────────────────────
"""

import struct
import time


# ─────────────────────────────────────────────────────────────────────────────
# *** EDIT THIS LIST TO ADD / CHANGE MPPTs ***
#
# Add effective device IDs in the order you want them indexed.
# To find an effective device ID: candump can0, take the 8-byte frame ID,
# shift right 4 bits. e.g. 0x020 >> 4 = 2, 0x100 >> 4 = 16.
# ─────────────────────────────────────────────────────────────────────────────

MPPT_EFFECTIVE_IDS = [17, 1, 3, 4, 5, 6]


# ─────────────────────────────────────────────────────────────────────────────
# Derived ID sets — do not edit, computed automatically from MPPT_EFFECTIVE_IDS
# ─────────────────────────────────────────────────────────────────────────────

MPPT_POWER_IDS  = [(eid << 4) | 0 for eid in MPPT_EFFECTIVE_IDS]
MPPT_STATUS_IDS = [(eid << 4) | 1 for eid in MPPT_EFFECTIVE_IDS]
MPPT_ALL_IDS    = set(MPPT_POWER_IDS + MPPT_STATUS_IDS)

# Reverse lookup: can_id -> mppt_index
_CAN_ID_TO_INDEX = {
    (eid << 4) | pkt_id: idx
    for idx, eid in enumerate(MPPT_EFFECTIVE_IDS)
    for pkt_id in (0, 1)
}

FAULT_NAMES = {
    0: "OK",
    1: "Config Error",
    2: "Input Over Voltage",
    3: "Output Over Voltage",
    4: "Output Over Current",
    5: "Input Over Current",
    6: "Input Under Current",
    7: "Phase Over Current",
}

MODE_NAMES = {
    0: "Const Vin",
    1: "Const Iin",
    2: "Min Iin",
    3: "Const Vout",
    4: "Const Iout",
    5: "Temp Derating",
    6: "Fault",
}


def _s16_be(data, offset):
    return struct.unpack_from(">h", data, offset)[0]


def _s8(data, offset):
    return struct.unpack_from("b", data, offset)[0]


def normalize_mppt_frame(raw_frame, device_id):
    """Decode one TPEE Open-SEC frame (power or status).

    device_id is the fleet-wide MPPT base. mppt_index (0-based position
    in MPPT_EFFECTIVE_IDS) is added so each board gets a unique device_id
    in InfluxDB.
    """
    can_id = raw_frame["can_id"]
    data = raw_frame["data"]
    pad = bytes(data) + b"\x00" * (8 - len(data)) if len(data) < 8 else bytes(data)

    mppt_index = _CAN_ID_TO_INDEX.get(can_id)
    if mppt_index is None:
        raise ValueError(
            f"normalize_mppt_frame: CAN ID 0x{can_id:X} not in MPPT_EFFECTIVE_IDS. "
            f"Add effective device ID {can_id >> 4} to MPPT_EFFECTIVE_IDS."
        )

    packet_id = can_id & 0x0F

    if packet_id == 0:
        # Packet ID 0 — Power measurements (every 0.5s, 8 bytes)
        # All signed INT16 big-endian per OpenSEC Manual V1.9
        input_voltage_v  = _s16_be(pad, 0) * 0.01
        input_current_a  = _s16_be(pad, 2) * 0.0005
        output_voltage_v = _s16_be(pad, 4) * 0.01
        output_current_a = _s16_be(pad, 6) * 0.0005
        pv_power_w       = round(input_voltage_v * input_current_a, 3)

        fields = {
            "pv_voltage_v":      input_voltage_v,
            "pv_current_a":      input_current_a,
            "pv_power_w":        pv_power_w,
            "battery_voltage_v": output_voltage_v,
            "battery_current_a": output_current_a,
        }

    elif packet_id == 1:
        # Packet ID 1 — Status (every 1.0s, 5 bytes)
        mode            = pad[0]
        fault           = pad[1]
        enabled         = pad[2]
        ambient_temp_c  = _s8(pad, 3)
        heatsink_temp_c = _s8(pad, 4)

        fields = {
            "mode":             float(mode),
            "mode_name":        MODE_NAMES.get(mode, f"Unknown({mode})"),
            "fault":            float(fault),
            "fault_name":       FAULT_NAMES.get(fault, f"Unknown({fault})"),
            "enabled":          float(enabled),
            "ambient_temp_c":   float(ambient_temp_c),
            "heatsink_temp_c":  float(heatsink_temp_c),
        }

    else:
        raise ValueError(
            f"normalize_mppt_frame: unhandled packet_id {packet_id} "
            f"from CAN ID 0x{can_id:X}"
        )

    fields["raw_hex"] = pad.hex()

    return {
        "device_type": "mppt",
        "device_id":   device_id + mppt_index,
        "mppt_index":  mppt_index,
        "packet_id":   packet_id,
        "timestamp":   int(raw_frame.get("rx_timestamp") or time.time()),
        "fields":      fields,
    }


# ─────────────────────────────────────────────────────────────────────────────
# BMS — EG4 LL-S in P06-LUX (Pylontech-compatible) mode
# ─────────────────────────────────────────────────────────────────────────────

BMS_LIMITS_ID    = 0x351
BMS_SOC_SOH_ID   = 0x355
BMS_LIVE_ID      = 0x356
BMS_ALARMS_ID    = 0x359
BMS_CHARGE_FLAGS = 0x35C
BMS_MFR_NAME_ID  = 0x35E

BMS_IDS = frozenset({
    BMS_LIMITS_ID, BMS_SOC_SOH_ID, BMS_LIVE_ID,
    BMS_ALARMS_ID, BMS_CHARGE_FLAGS, BMS_MFR_NAME_ID,
})


def _u16_le(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _s16_le(data, offset):
    return struct.unpack_from("<h", data, offset)[0]


def normalize_bms_frame(raw_frame, device_id):
    """Decode one Pylontech-style BMS frame."""
    can_id = raw_frame["can_id"]
    data = bytes(raw_frame["data"])
    if can_id not in BMS_IDS:
        raise ValueError(f"normalize_bms_frame got non-BMS id 0x{can_id:X}")

    pad = data + b"\x00" * (8 - len(data)) if len(data) < 8 else data
    fields = {"can_id_hex": f"0x{can_id:X}"}

    if can_id == BMS_LIMITS_ID:
        fields.update({
            "charge_voltage_v":    _u16_le(pad, 0) * 0.1,
            "charge_current_a":    _s16_le(pad, 2) * 0.1,
            "discharge_current_a": _s16_le(pad, 4) * 0.1,
            "discharge_voltage_v": _u16_le(pad, 6) * 0.1,
        })
    elif can_id == BMS_SOC_SOH_ID:
        fields.update({
            "soc_pct": _u16_le(pad, 0),
            "soh_pct": _u16_le(pad, 2),
        })
    elif can_id == BMS_LIVE_ID:
        fields.update({
            "battery_voltage_v": _s16_le(pad, 0) * 0.01,
            "battery_current_a": _s16_le(pad, 2) * 0.1,
            "battery_temp_c":    _s16_le(pad, 4) * 0.1,
        })
    elif can_id == BMS_ALARMS_ID:
        fields.update({
            "protection_flags": _u16_le(pad, 0),
            "alarm_flags":      _u16_le(pad, 2),
            "module_count":     pad[4],
        })
    elif can_id == BMS_CHARGE_FLAGS:
        b0 = pad[0]
        fields.update({
            "charge_enable":      int(bool(b0 & 0x80)),
            "discharge_enable":   int(bool(b0 & 0x40)),
            "force_charge_req_1": int(bool(b0 & 0x20)),
            "force_charge_req_2": int(bool(b0 & 0x10)),
        })
    elif can_id == BMS_MFR_NAME_ID:
        try:
            mfr = pad.decode("ascii", errors="replace").strip()
        except Exception:
            mfr = pad.hex()
        fields["manufacturer"] = mfr

    fields["raw_hex"] = pad.hex()

    return {
        "device_type": "bms",
        "device_id":   device_id,
        "timestamp":   int(raw_frame.get("rx_timestamp") or time.time()),
        "fields":      fields,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: default id_to_kind mapping for the CANReader
# ─────────────────────────────────────────────────────────────────────────────

def default_id_to_kind(num_mppts=None):
    """Build the {can_id: kind} dict from MPPT_EFFECTIVE_IDS + BMS_IDS.

    num_mppts is ignored — all IDs in MPPT_EFFECTIVE_IDS are always included.
    The parameter is kept for backwards compatibility only.
    """
    mapping = {can_id: "bms" for can_id in BMS_IDS}
    for eid in MPPT_EFFECTIVE_IDS:
        mapping[(eid << 4) | 0] = "mppt"  # power frame
        mapping[(eid << 4) | 1] = "mppt"  # status frame
    return mapping
