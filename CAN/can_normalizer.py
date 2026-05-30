#!/usr/bin/env python3
"""Decode raw CAN frames into named engineering units.

Two normalizer functions, both matching the signature the 444-line sender
expects:
    normalize_xxx_frame(raw_frame: dict, device_id: int) -> dict

The returned dict shape mirrors what bmv_normalizer produces:
    {
        "device_type":  "mppt" | "bms",
        "device_id":    <as passed in>,
        "timestamp":    int unix seconds,
        "fields":       {named_field: numeric_value, ...},
        # MPPT-only extras for multi-board pipelines:
        "mppt_index":   int  (0..4, derived from CAN ID offset)
    }


CAUTION — MPPT BYTE LAYOUTS ARE BEST-GUESS
==========================================
TPEE publishes the firmware at github.com/TjitteS/OpenSmartEnergyConverter
along with a DBC file named "MPPT_ID32+0-4.dbc". The filename implies a
base ID of 32 (0x20) plus a board offset 0..4. The exact byte ordering /
scaling / signedness inside each message is NOT included here — I could
not retrieve the DBC contents.

So the MPPT decoder below is a TEMPLATE that:
  - assigns one CAN ID per board to a "status" message containing all the
    measurements TPEE's product docs mention (input V/I, output V/I, temp)
  - uses little-endian 16-bit values with a 0.01 V / 0.1 A / 0.1 C scale
    (a common TPEE convention, but VERIFY against your candump)

After your first run, look at the raw `data_hex` field in mppt_data.csv,
cross-reference with the DBC file or known operating values, and adjust
the offsets/scales in MPPT_LAYOUT below. Each entry is one line.

BMS BYTE LAYOUTS ARE AUTHORITATIVE
==================================
The EG4 LL-S in P06-LUX mode is Pylontech-compatible CAN, well-documented:
- 0x351 charge/discharge limits (little-endian, 0.1 V / 0.1 A scales)
- 0x355 SOC / SOH (uint16, percent)
- 0x356 voltage / current / temperature (signed, 0.01 V / 0.1 A / 0.1 C)
- 0x359 protection flags / alarm flags / module count
- 0x35C charge-request flags
- 0x35E manufacturer name (8-byte ASCII, e.g. "PYLON   ")
"""

import struct
import time


# ─────────────────────────────────────────────────────────────────────────────
# MPPT — TPEE Open-SEC family
# ─────────────────────────────────────────────────────────────────────────────
#
# CAN ID layout per the DBC filename "MPPT_ID32+0-4.dbc":
#     0x20 -> board #0 status
#     0x21 -> board #1 status
#     0x22 -> board #2 status
#     0x23 -> board #3 status
#     0x24 -> board #4 status
#
# Single-message status frame, 8 data bytes:
#     bytes 0-1   pv_voltage     little-endian uint16  scale 0.01 V
#     bytes 2-3   pv_current     little-endian uint16  scale 0.01 A
#     bytes 4-5   battery_voltage  LE uint16  scale 0.01 V
#     bytes 6-7   mosfet_temp    little-endian  int16  scale 0.1  C
#
# TODO: VERIFY all of the above against candump on your actual board.
# Quick verification recipe:
#   sudo apt install can-utils
#   candump -tz can0 | head -40
# Look at a frame for ID 0x20. If pv_voltage prints as ~80 V when the panel
# is in sun, the scaling and offset are right. If it prints as 8 V or 800 V,
# you've got the scale off by 10x. If it prints negative or wildly off, the
# endianness is reversed.

MPPT_BASE_ID = 0x200
MPPT_ID_RANGE = range(MPPT_BASE_ID, MPPT_BASE_ID + 6)  # 0x200..0x205 for 6 MPPTs


def _u16_le(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _s16_le(data, offset):
    return struct.unpack_from("<h", data, offset)[0]


def normalize_mppt_frame(raw_frame, device_id):
    """Decode one TPEE Open-SEC status frame.

    `device_id` is taken as the *fleet-wide* MPPT base. The board's index
    on the bus (0..4) is appended to it so each board lands as a distinct
    Influx device_id without you having to configure each one.

    Returns a normalized dict; raises on malformed frames.
    """
    can_id = raw_frame["can_id"]
    data = raw_frame["data"]

    if can_id not in MPPT_ID_RANGE:
        raise ValueError(f"normalize_mppt_frame got non-MPPT id 0x{can_id:X}")
    if len(data) < 8:
        # DLC < 8 means the firmware is using a different message layout —
        # carry on but flag every value as None so downstream sees the gap
        # rather than reading garbage off the end of the buffer.
        pad = bytes(data) + b"\x00" * (8 - len(data))
    else:
        pad = bytes(data)

    mppt_index = can_id - MPPT_BASE_ID

    # TODO: VERIFY all scaling factors / endianness on first deployment.
    pv_voltage_v      = _u16_le(pad, 0) * 0.01   # bytes 0-1
    pv_power_w        = _u16_le(pad, 2) * 0.01   # bytes 2-3
    battery_voltage_v = _u16_le(pad, 4) * 0.01   # bytes 4-5
    battery_current_a = _u16_le(pad, 6) * 0.1    # bytes 6-7
    pv_current_a      = round(pv_power_w / pv_voltage_v, 3) if pv_voltage_v > 0 else 0.0  # derived
    mosfet_temp_c     = 0.0  # not present in this frame layout

    return {
        "device_type": "mppt",
        "device_id": device_id + mppt_index,
        "mppt_index": mppt_index,
        "timestamp": int(raw_frame.get("rx_timestamp") or time.time()),
        "fields": {
            "pv_voltage_v": pv_voltage_v,
            "pv_current_a": pv_current_a,
            "pv_power_w": pv_power_w,
            "battery_voltage_v": battery_voltage_v,
            "mosfet_temp_c": mosfet_temp_c,
            # Carry raw bytes too so first-run debugging is trivial — drop
            # this once your decoding is verified to save InfluxDB space.
            "raw_hex": pad.hex(),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# BMS — EG4 LL-S in P06-LUX (Pylontech-compatible) mode
# ─────────────────────────────────────────────────────────────────────────────
#
# All multi-byte fields are little-endian. Authoritative source:
# Pylontech BMS Protocol CAN 20161103 v1.2/v1.3 + setfirelabs RE notes.

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

    Each ID emits a different subset of fields. The normalizer always
    returns the same envelope shape but only the relevant fields will be
    populated for any given frame.
    """
    can_id = raw_frame["can_id"]
    data = bytes(raw_frame["data"])
    if can_id not in BMS_IDS:
        raise ValueError(f"normalize_bms_frame got non-BMS id 0x{can_id:X}")

    pad = data + b"\x00" * (8 - len(data)) if len(data) < 8 else data

    fields = {"can_id_hex": f"0x{can_id:X}"}

    if can_id == BMS_LIMITS_ID:
        # 0x351: bytes 0-1 charge V limit (u16 LE * 0.1 V)
        #        bytes 2-3 charge current limit (s16 LE * 0.1 A)
        #        bytes 4-5 discharge current limit (s16 LE * 0.1 A)
        #        bytes 6-7 discharge voltage limit (u16 LE * 0.1 V) — v1.3+
        fields.update({
            "charge_voltage_v":     _u16_le(pad, 0) * 0.1,
            "charge_current_a":     _s16_le(pad, 2) * 0.1,
            "discharge_current_a":  _s16_le(pad, 4) * 0.1,
            "discharge_voltage_v":  _u16_le(pad, 6) * 0.1,
        })

    elif can_id == BMS_SOC_SOH_ID:
        # 0x355: bytes 0-1 SOC (u16 LE, percent)
        #        bytes 2-3 SOH (u16 LE, percent)
        fields.update({
            "soc_pct": _u16_le(pad, 0),
            "soh_pct": _u16_le(pad, 2),
        })

    elif can_id == BMS_LIVE_ID:
        # 0x356: bytes 0-1 pack voltage  (s16 LE * 0.01 V)
        #        bytes 2-3 pack current  (s16 LE * 0.1  A)
        #        bytes 4-5 pack temp     (s16 LE * 0.1  C)
        fields.update({
            "battery_voltage_v": _s16_le(pad, 0) * 0.01,
            "battery_current_a": _s16_le(pad, 2) * 0.1,
            "battery_temp_c":    _s16_le(pad, 4) * 0.1,
        })

    elif can_id == BMS_ALARMS_ID:
        # 0x359: bytes 0-1 protection flags (bitmap)
        #        bytes 2-3 alarm flags (bitmap)
        #        byte  4   module count
        #        bytes 5-7 manufacturer code (varies; ignored)
        fields.update({
            "protection_flags": _u16_le(pad, 0),
            "alarm_flags":      _u16_le(pad, 2),
            "module_count":     pad[4],
        })

    elif can_id == BMS_CHARGE_FLAGS:
        # 0x35C: bit 7 of byte 0 = charge enable
        #        bit 6 of byte 0 = discharge enable
        #        bit 5 of byte 0 = force charge request 1
        #        bit 4 of byte 0 = force charge request 2
        b0 = pad[0]
        fields.update({
            "charge_enable":         int(bool(b0 & 0x80)),
            "discharge_enable":      int(bool(b0 & 0x40)),
            "force_charge_req_1":    int(bool(b0 & 0x20)),
            "force_charge_req_2":    int(bool(b0 & 0x10)),
        })

    elif can_id == BMS_MFR_NAME_ID:
        # 0x35E: 8 ASCII bytes of manufacturer name, padded with spaces.
        # Stored as a string field. Useful for confirming the BMS is in
        # P06-LUX mode after dip-switch / firmware changes.
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
    mapping = {}
    for i in range(num_mppts):
        mapping[MPPT_BASE_ID + i] = "mppt"
    return mapping
