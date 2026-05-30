#!/usr/bin/env python3
"""Decode raw CAN frames into named engineering units.

Two normalizer functions, both matching the signature the sender expects:
    normalize_xxx_frame(raw_frame: dict, device_id: int) -> dict

MPPT BYTE LAYOUT — VERIFIED against candump:
    Frame ID:  0x200 = MPPT #0, 0x201 = MPPT #1, etc.
    bytes 0-1  pv_voltage_v      BE uint16  * 0.01 V   (confirmed: 0x0781 -> 19.21V)
    bytes 2-3  pv_power_w        BE uint16  * 0.01 W   (confirmed: 0x02E5 ->  7.41W)
    bytes 4-5  battery_voltage_v BE uint16  * 0.01 V   (confirmed: 0x14E9 -> 53.53V)
    byte  6    battery_current_a uint8      * 0.1  A   (confirmed: 0x01   ->  0.1A)
    byte  7    status_byte       uint8      raw         (unknown, stored for debugging)
    pv_current_a is derived: pv_power_w / pv_voltage_v

BMS BYTE LAYOUT — EG4 LL-S in P06-LUX (Pylontech-compatible) mode:
    Authoritative source: Pylontech BMS CAN Protocol v1.2/v1.3
"""

import struct
import time


# ─────────────────────────────────────────────────────────────────────────────
# MPPT — TPEE Open-SEC, verified byte layout
# ─────────────────────────────────────────────────────────────────────────────

MPPT_BASE_ID = 0x200
MPPT_ID_RANGE = range(MPPT_BASE_ID, MPPT_BASE_ID + 6)  # 0x200..0x205 for 6 MPPTs


def _u16_be(data, offset):
    return struct.unpack_from(">H", data, offset)[0]


def _u16_le(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _s16_le(data, offset):
    return struct.unpack_from("<h", data, offset)[0]


def normalize_mppt_frame(raw_frame, device_id):
    """Decode one TPEE MPPT status frame.

    device_id is the fleet-wide MPPT base ID. The board index (0..5) is
    added so each board appears as a distinct device_id in InfluxDB.
    """
    can_id = raw_frame["can_id"]
    data = raw_frame["data"]

    if can_id not in MPPT_ID_RANGE:
        raise ValueError(f"normalize_mppt_frame got non-MPPT id 0x{can_id:X}")

    pad = bytes(data) + b"\x00" * (8 - len(data)) if len(data) < 8 else bytes(data)

    mppt_index = can_id - MPPT_BASE_ID

    pv_voltage_v      = _u16_be(pad, 0) * 0.01   # bytes 0-1 BE: confirmed ~19V
    pv_power_w        = _u16_be(pad, 2) * 0.01   # bytes 2-3 BE: confirmed ~7W
    battery_voltage_v = _u16_be(pad, 4) * 0.01   # bytes 4-5 BE: confirmed ~53V
    battery_current_a = pad[6] * 0.1             # byte 6 only: confirmed 0.1A
    status_byte       = pad[7]                   # byte 7: unknown, stored raw
    pv_current_a      = round(pv_power_w / pv_voltage_v, 3) if pv_voltage_v > 0 else 0.0

    return {
        "device_type": "mppt",
        "device_id": device_id + mppt_index,
        "mppt_index": mppt_index,
        "timestamp": int(raw_frame.get("rx_timestamp") or time.time()),
        "fields": {
            "pv_voltage_v":      pv_voltage_v,
            "pv_power_w":        pv_power_w,
            "pv_current_a":      pv_current_a,
            "battery_voltage_v": battery_voltage_v,
            "battery_current_a": battery_current_a,
            "status_byte":       float(status_byte),
            "raw_hex":           pad.hex(),
        },
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


def normalize_bms_frame(raw_frame, device_id):
    """Decode one Pylontech-style BMS frame.

    Each CAN ID carries a different subset of fields. Only the relevant
    fields are populated for any given frame.
    """
    can_id = raw_frame["can_id"]
    data = bytes(raw_frame["data"])
    if can_id not in BMS_IDS:
        raise ValueError(f"normalize_bms_frame got non-BMS id 0x{can_id:X}")

    pad = data + b"\x00" * (8 - len(data)) if len(data) < 8 else data
    fields = {"can_id_hex": f"0x{can_id:X}"}

    if can_id == BMS_LIMITS_ID:
        # 0x351: charge/discharge voltage and current limits
        fields.update({
            "charge_voltage_v":    _u16_le(pad, 0) * 0.1,
            "charge_current_a":    _s16_le(pad, 2) * 0.1,
            "discharge_current_a": _s16_le(pad, 4) * 0.1,
            "discharge_voltage_v": _u16_le(pad, 6) * 0.1,
        })

    elif can_id == BMS_SOC_SOH_ID:
        # 0x355: state of charge / state of health
        fields.update({
            "soc_pct": _u16_le(pad, 0),
            "soh_pct": _u16_le(pad, 2),
        })

    elif can_id == BMS_LIVE_ID:
        # 0x356: live pack voltage, current, temperature
        fields.update({
            "battery_voltage_v": _s16_le(pad, 0) * 0.01,
            "battery_current_a": _s16_le(pad, 2) * 0.1,
            "battery_temp_c":    _s16_le(pad, 4) * 0.1,
        })

    elif can_id == BMS_ALARMS_ID:
        # 0x359: protection flags, alarm flags, module count
        fields.update({
            "protection_flags": _u16_le(pad, 0),
            "alarm_flags":      _u16_le(pad, 2),
            "module_count":     pad[4],
        })

    elif can_id == BMS_CHARGE_FLAGS:
        # 0x35C: charge/discharge enable flags
        b0 = pad[0]
        fields.update({
            "charge_enable":      int(bool(b0 & 0x80)),
            "discharge_enable":   int(bool(b0 & 0x40)),
            "force_charge_req_1": int(bool(b0 & 0x20)),
            "force_charge_req_2": int(bool(b0 & 0x10)),
        })

    elif can_id == BMS_MFR_NAME_ID:
        # 0x35E: 8-byte ASCII manufacturer name
        try:
            mfr = pad.decode("ascii", errors="replace").strip()
        except Exception:
            mfr = pad.hex()
        fields["manufacturer"] = mfr

    fields["raw_hex"] = pad.hex()

    return {
        "device_type": "bms",
        "device_id": device_id,
        "timestamp": int(raw_frame.get("rx_timestamp") or time.time()),
        "fields": fields,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: default id_to_kind mapping for the CANReader
# ─────────────────────────────────────────────────────────────────────────────

def default_id_to_kind(num_mppts=6):
    """Build the {can_id: kind} dict for one BMS pack + N MPPTs.

    Pass to CANReader(id_to_kind=...).
    """
    mapping = {can_id: "bms" for can_id in BMS_IDS}
    for i in range(num_mppts):
        mapping[MPPT_BASE_ID + i] = "mppt"
    return mapping
