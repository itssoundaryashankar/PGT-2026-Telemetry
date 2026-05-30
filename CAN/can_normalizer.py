#!/usr/bin/env python3
"""Decode raw CAN frames into named engineering units.

TPEE Open-SEC CAN protocol — authoritative source: OpenSEC Manual V1.9

CAN ID formula:  can_id = (device_id << 4) | packet_id
Default device ID = 32 (0x20), encoder = 0, so:
    MPPT #0: power=0x200, status=0x201
    MPPT #1: power=0x210, status=0x211
    MPPT #2: power=0x220, status=0x221
    MPPT #3: power=0x230, status=0x231
    MPPT #4: power=0x240, status=0x241
    MPPT #5: power=0x250, status=0x251

Packet ID 0 — Power Measurements (every 0.5s, 8 bytes):
    bytes 0-1  input_voltage_v    INT16 BE  * 0.01    V    (PV panel voltage)
    bytes 2-3  input_current_a    INT16 BE  * 0.0005  A    (PV panel current)
    bytes 4-5  output_voltage_v   INT16 BE  * 0.01    V    (battery voltage)
    bytes 6-7  output_current_a   INT16 BE  * 0.0005  A    (battery current)

Packet ID 1 — Status (every 1.0s, 5 bytes):
    byte  0    mode       UINT8  (0=Const Vin, 1=Const Iin, 2=Min Iin,
                                  3=Const Vout, 4=Const Iout,
                                  5=Temp Derating, 6=Fault)
    byte  1    fault      UINT8  (0=OK, 1=Config, 2=In OV, 3=Out OV,
                                  4=Out OC, 5=In OC, 6=In UC, 7=Phase OC)
    byte  2    enabled    UINT8  (0=Disabled, 1=Enabled)
    byte  3    ambient_temp_c   INT8  * 1 C
    byte  4    heatsink_temp_c  INT8  * 1 C

BMS BYTE LAYOUT — EG4 LL-S in P06-LUX (Pylontech-compatible) mode.
"""

import struct
import time


# ─────────────────────────────────────────────────────────────────────────────
# MPPT — TPEE Open-SEC authoritative layout
# ─────────────────────────────────────────────────────────────────────────────

MPPT_BASE_DEVICE_ID = 1
NUM_MPPTS = 6

MPPT_POWER_IDS  = [((MPPT_BASE_DEVICE_ID + i) << 4) | 0 for i in range(NUM_MPPTS)]
MPPT_STATUS_IDS = [((MPPT_BASE_DEVICE_ID + i) << 4) | 1 for i in range(NUM_MPPTS)]
MPPT_ALL_IDS    = set(MPPT_POWER_IDS + MPPT_STATUS_IDS)

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

    device_id is the fleet-wide MPPT base. Board index (0..5) is added
    so each board appears as a distinct device_id in InfluxDB.
    """
    can_id = raw_frame["can_id"]
    data = raw_frame["data"]
    pad = bytes(data) + b"\x00" * (8 - len(data)) if len(data) < 8 else bytes(data)

    mppt_index = (can_id >> 4) - MPPT_BASE_DEVICE_ID
    packet_id  = can_id & 0x0F

    if packet_id == 0:
        # Power measurements — 8 bytes, all signed INT16 big-endian
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
        # Status — 5 bytes
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

def default_id_to_kind(num_mppts=NUM_MPPTS):
    """Build the {can_id: kind} dict for one BMS pack + N MPPTs.

    Includes both power (packet 0) and status (packet 1) for each MPPT.
    """
    mapping = {can_id: "bms" for can_id in BMS_IDS}
    for i in range(num_mppts):
        mapping[((MPPT_BASE_DEVICE_ID + i) << 4) | 0] = "mppt"
        mapping[((MPPT_BASE_DEVICE_ID + i) << 4) | 1] = "mppt"
    return mapping
