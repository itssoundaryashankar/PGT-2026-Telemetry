#!/usr/bin/env python3
"""Binary wire format for the telemetry pipeline.

Layout (unchanged for BMV — additive change for MPPT and BMS):

    header (14 bytes, big-endian)
        u8   protocol version
        u8   msg_type (1=BMV, 2=MPPT, 3=BMS)
        u8   event_type
        u8   device_id
        u16  seq
        u32  timestamp (unix seconds)
        u16  field_mask  (per-msg-type bitmask, see *_FIELD_LAYOUT below)
    payload (variable)
        struct-packed values, in *_FIELD_LAYOUT order, only those whose
        bit is set in field_mask.
    crc (2 bytes)
        CRC-16/XMODEM over header+payload.

To add another field to MPPT/BMS without breaking older receivers: append
a new (bit, name, fmt) tuple at the end of the layout tuple. Older
receivers will see the new bit set, fail to recognise it, and reject the
packet — protocol version stays at 1 because the *format* is the same, only
the field set grows.
"""

import binascii
import struct
from enum import IntEnum


PROTOCOL_VERSION = 1
CRC_FORMAT = ">H"
HEADER_FORMAT = ">BBBBHIH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
CRC_SIZE = struct.calcsize(CRC_FORMAT)


class MsgType(IntEnum):
    BMV = 1
    MPPT = 2
    BMS = 3


class EventType(IntEnum):
    SAMPLE = 1
    DELTA_UPDATE = 2
    THRESHOLD_CROSSING = 3
    ALARM = 4
    HEARTBEAT = 5
    DEVICE_STATUS = 6


# ─────────────────────────────────────────────────────────────────────────────
# Per-msg-type field layouts
# ─────────────────────────────────────────────────────────────────────────────
#
# Format of each entry:  (bit_flag, normalized_field_name, struct_format)
# - The struct format determines how the value is packed on the wire AND
#   what unit/scale it carries. Values are scaled (multiplied) before
#   packing and unscaled (divided) after unpacking.
# - Scaling lets us fit a float into a small int and save LoRa airtime.
#   See _PACK_SCALES below.
#
# Adding a new field is one line; never remove or reorder existing entries.

class BMVField(IntEnum):
    VOLTAGE_MV = 1 << 0
    CURRENT_MA = 1 << 1
    POWER_W = 1 << 2
    CHARGE_STATE = 1 << 3
    ALARM = 1 << 4


BMV_FIELD_LAYOUT = (
    (BMVField.VOLTAGE_MV, "voltage_mv", ">H"),
    (BMVField.CURRENT_MA, "current_ma", ">h"),
    (BMVField.POWER_W,    "power_w",    ">h"),
    (BMVField.CHARGE_STATE, "charge_state", ">B"),
    (BMVField.ALARM,      "alarm",      ">B"),
)


class MPPTField(IntEnum):
    PV_VOLTAGE_V       = 1 << 0
    PV_CURRENT_A       = 1 << 1
    PV_POWER_W         = 1 << 2
    BATTERY_VOLTAGE_V  = 1 << 3
    MOSFET_TEMP_C      = 1 << 4
    MPPT_INDEX         = 1 << 5


# For MPPT we send floats as scaled ints to keep packets tiny.
# 0.01 V resolution on a 200 V scale -> 20000 fits in u16.
# 0.01 A resolution on a 100 A scale -> 10000 fits in u16.
# Temperature: 0.1 C resolution, signed -> s16.
MPPT_FIELD_LAYOUT = (
    (MPPTField.PV_VOLTAGE_V,      "pv_voltage_v",      ">H"),  # *100
    (MPPTField.PV_CURRENT_A,      "pv_current_a",      ">H"),  # *100
    (MPPTField.PV_POWER_W,        "pv_power_w",        ">H"),  # *1
    (MPPTField.BATTERY_VOLTAGE_V, "battery_voltage_v", ">H"),  # *100
    (MPPTField.MOSFET_TEMP_C,     "mosfet_temp_c",     ">h"),  # *10
    (MPPTField.MPPT_INDEX,        "mppt_index",        ">B"),  # *1
)


class BMSField(IntEnum):
    CHARGE_VOLTAGE_V     = 1 << 0
    CHARGE_CURRENT_A     = 1 << 1
    DISCHARGE_CURRENT_A  = 1 << 2
    DISCHARGE_VOLTAGE_V  = 1 << 3
    SOC_PCT              = 1 << 4
    SOH_PCT              = 1 << 5
    BATTERY_VOLTAGE_V    = 1 << 6
    BATTERY_CURRENT_A    = 1 << 7
    BATTERY_TEMP_C       = 1 << 8
    PROTECTION_FLAGS     = 1 << 9
    ALARM_FLAGS          = 1 << 10
    MODULE_COUNT         = 1 << 11
    CHARGE_ENABLE        = 1 << 12
    DISCHARGE_ENABLE     = 1 << 13


BMS_FIELD_LAYOUT = (
    (BMSField.CHARGE_VOLTAGE_V,    "charge_voltage_v",    ">H"),  # *10
    (BMSField.CHARGE_CURRENT_A,    "charge_current_a",    ">h"),  # *10
    (BMSField.DISCHARGE_CURRENT_A, "discharge_current_a", ">h"),  # *10
    (BMSField.DISCHARGE_VOLTAGE_V, "discharge_voltage_v", ">H"),  # *10
    (BMSField.SOC_PCT,             "soc_pct",             ">B"),  # *1
    (BMSField.SOH_PCT,             "soh_pct",             ">B"),  # *1
    (BMSField.BATTERY_VOLTAGE_V,   "battery_voltage_v",   ">H"),  # *100
    (BMSField.BATTERY_CURRENT_A,   "battery_current_a",   ">h"),  # *10
    (BMSField.BATTERY_TEMP_C,      "battery_temp_c",      ">h"),  # *10
    (BMSField.PROTECTION_FLAGS,    "protection_flags",    ">H"),  # *1
    (BMSField.ALARM_FLAGS,         "alarm_flags",         ">H"),  # *1
    (BMSField.MODULE_COUNT,        "module_count",        ">B"),  # *1
    (BMSField.CHARGE_ENABLE,       "charge_enable",       ">B"),  # *1
    (BMSField.DISCHARGE_ENABLE,    "discharge_enable",    ">B"),  # *1
)


# Per-field multiplicative scale: encoded_int = round(value * scale)
# Anything not listed defaults to 1.
_PACK_SCALES = {
    # MPPT
    "pv_voltage_v":      100,
    "pv_current_a":      100,
    "pv_power_w":        1,
    "battery_voltage_v_mppt": 100,  # never used as a key; see _scale_for()
    "mosfet_temp_c":     10,
    "mppt_index":        1,
    # BMS
    "charge_voltage_v":     10,
    "charge_current_a":     10,
    "discharge_current_a":  10,
    "discharge_voltage_v":  10,
    "battery_voltage_v":    100,
    "battery_current_a":    10,
    "battery_temp_c":       10,
    "soc_pct":              1,
    "soh_pct":              1,
    "protection_flags":     1,
    "alarm_flags":          1,
    "module_count":         1,
    "charge_enable":        1,
    "discharge_enable":     1,
}


def _scale_for(field_name):
    return _PACK_SCALES.get(field_name, 1)


# Table for decode dispatch: msg_type -> field layout tuple.
_LAYOUTS = {
    MsgType.BMV:  BMV_FIELD_LAYOUT,
    MsgType.MPPT: MPPT_FIELD_LAYOUT,
    MsgType.BMS:  BMS_FIELD_LAYOUT,
}

# BMV uses raw ints (mv, ma, w) without further scaling — preserve that.
_RAW_INT_MSG_TYPES = frozenset({MsgType.BMV})


# ─────────────────────────────────────────────────────────────────────────────
# CRC + header packing
# ─────────────────────────────────────────────────────────────────────────────

def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


def _pack_header(msg_type, event_type, device_id, seq, timestamp, field_mask):
    return struct.pack(
        HEADER_FORMAT,
        PROTOCOL_VERSION,
        int(msg_type),
        int(event_type),
        int(device_id) & 0xFF,
        int(seq) & 0xFFFF,
        int(timestamp) & 0xFFFFFFFF,
        int(field_mask) & 0xFFFF,
    )


def _wrap_with_crc(packet_wo_crc: bytes) -> bytes:
    return packet_wo_crc + struct.pack(CRC_FORMAT, crc16(packet_wo_crc))


# ─────────────────────────────────────────────────────────────────────────────
# Generic packet builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_typed_packet(normalized, event_type, seq, msg_type, layout,
                        device_type_expected):
    if normalized.get("device_type") != device_type_expected:
        raise ValueError(
            f"Normalized payload device_type={normalized.get('device_type')!r}, "
            f"expected {device_type_expected!r}"
        )

    fields = normalized.get("fields", {})
    field_mask = 0
    payload = bytearray()

    use_raw_ints = msg_type in _RAW_INT_MSG_TYPES

    # MPPT carries its index outside `fields` for normalizer convenience —
    # pull it in so it can be packed if the layout includes it.
    if "mppt_index" in normalized and "mppt_index" not in fields:
        fields = dict(fields)
        fields["mppt_index"] = normalized["mppt_index"]

    for bit, field_name, fmt in layout:
        if field_name not in fields or fields[field_name] is None:
            continue
        value = fields[field_name]
        if use_raw_ints:
            encoded = int(value)
        else:
            encoded = int(round(float(value) * _scale_for(field_name)))
        try:
            payload.extend(struct.pack(fmt, encoded))
        except struct.error as exc:
            raise ValueError(
                f"Cannot pack field {field_name}={value} (encoded {encoded}, "
                f"fmt {fmt}): {exc}"
            ) from exc
        field_mask |= int(bit)

    header = _pack_header(
        msg_type, event_type, normalized["device_id"],
        seq, normalized["timestamp"], field_mask,
    )
    return _wrap_with_crc(header + bytes(payload))


def build_bmv_packet(normalized, event_type, seq):
    return _build_typed_packet(
        normalized, event_type, seq, MsgType.BMV, BMV_FIELD_LAYOUT, "bmv"
    )


def build_mppt_packet(normalized, event_type, seq):
    return _build_typed_packet(
        normalized, event_type, seq, MsgType.MPPT, MPPT_FIELD_LAYOUT, "mppt"
    )


def build_bms_packet(normalized, event_type, seq):
    return _build_typed_packet(
        normalized, event_type, seq, MsgType.BMS, BMS_FIELD_LAYOUT, "bms"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Decode
# ─────────────────────────────────────────────────────────────────────────────

def decode_packet(packet: bytes) -> dict:
    if len(packet) < HEADER_SIZE + CRC_SIZE:
        raise ValueError("Packet too short")

    payload_end = len(packet) - CRC_SIZE
    packet_wo_crc = packet[:payload_end]
    expected_crc = struct.unpack(CRC_FORMAT, packet[payload_end:])[0]
    actual_crc = crc16(packet_wo_crc)
    if actual_crc != expected_crc:
        raise ValueError(
            f"CRC mismatch: expected {expected_crc:#06x}, got {actual_crc:#06x}"
        )

    version, msg_type, event_type, device_id, seq, timestamp, field_mask = struct.unpack(
        HEADER_FORMAT, packet[:HEADER_SIZE]
    )
    if version != PROTOCOL_VERSION:
        raise ValueError(f"Unsupported protocol version {version}")

    payload = packet[HEADER_SIZE:payload_end]
    fields = decode_payload(msg_type, field_mask, payload)

    return {
        "version": version,
        "msg_type": MsgType(msg_type),
        "event_type": EventType(event_type),
        "device_id": device_id,
        "seq": seq,
        "timestamp": timestamp,
        "field_mask": field_mask,
        "fields": fields,
    }


def decode_payload(msg_type, field_mask, payload):
    try:
        mt = MsgType(msg_type)
    except ValueError as exc:
        raise ValueError(f"Unsupported msg_type {msg_type}") from exc

    layout = _LAYOUTS.get(mt)
    if layout is None:
        raise ValueError(f"No layout registered for msg_type {mt.name}")

    use_raw_ints = mt in _RAW_INT_MSG_TYPES

    fields = {}
    offset = 0
    for bit, field_name, fmt in layout:
        if not (field_mask & int(bit)):
            continue
        size = struct.calcsize(fmt)
        if offset + size > len(payload):
            raise ValueError(
                f"Payload shorter than field mask indicates "
                f"(want {field_name}, need {size}, have {len(payload) - offset})"
            )
        encoded = struct.unpack(fmt, payload[offset:offset + size])[0]
        offset += size
        if use_raw_ints:
            fields[field_name] = encoded
        else:
            scale = _scale_for(field_name)
            fields[field_name] = encoded / scale if scale != 1 else encoded

    if offset != len(payload):
        raise ValueError("Payload has unexpected trailing bytes")
    return fields
