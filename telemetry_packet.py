#!/usr/bin/env python3
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


class EventType(IntEnum):
    SAMPLE = 1
    DELTA_UPDATE = 2
    THRESHOLD_CROSSING = 3
    ALARM = 4
    HEARTBEAT = 5
    DEVICE_STATUS = 6


class BMVField(IntEnum):
    VOLTAGE_MV = 1 << 0
    CURRENT_MA = 1 << 1
    POWER_W = 1 << 2
    CHARGE_STATE = 1 << 3
    ALARM = 1 << 4


BMV_FIELD_LAYOUT = (
    (BMVField.VOLTAGE_MV, "voltage_mv", ">H"),
    (BMVField.CURRENT_MA, "current_ma", ">h"),
    (BMVField.POWER_W, "power_w", ">h"),
    (BMVField.CHARGE_STATE, "charge_state", ">B"),
    (BMVField.ALARM, "alarm", ">B"),
)


def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


def build_bmv_packet(normalized: dict, event_type: EventType, seq: int) -> bytes:
    if normalized.get("device_type") != "bmv":
        raise ValueError("Normalized payload is not a BMV packet")

    fields = normalized.get("fields", {})
    field_mask = 0
    payload = bytearray()

    for bit, field_name, fmt in BMV_FIELD_LAYOUT:
        if field_name not in fields or fields[field_name] is None:
            continue
        field_mask |= int(bit)
        payload.extend(struct.pack(fmt, int(fields[field_name])))

    header = struct.pack(
        HEADER_FORMAT,
        PROTOCOL_VERSION,
        int(MsgType.BMV),
        int(event_type),
        int(normalized["device_id"]),
        int(seq) & 0xFFFF,
        int(normalized["timestamp"]),
        field_mask,
    )
    packet_wo_crc = header + bytes(payload)
    packet_crc = struct.pack(CRC_FORMAT, crc16(packet_wo_crc))
    return packet_wo_crc + packet_crc


def decode_packet(packet: bytes) -> dict:
    if len(packet) < HEADER_SIZE + CRC_SIZE:
        raise ValueError("Packet too short")

    payload_end = len(packet) - CRC_SIZE
    packet_wo_crc = packet[:payload_end]
    expected_crc = struct.unpack(CRC_FORMAT, packet[payload_end:])[0]
    actual_crc = crc16(packet_wo_crc)

    if actual_crc != expected_crc:
        raise ValueError(f"CRC mismatch: expected {expected_crc:#06x}, got {actual_crc:#06x}")

    version, msg_type, event_type, device_id, seq, timestamp, field_mask = struct.unpack(
        HEADER_FORMAT, packet[:HEADER_SIZE]
    )

    if version != PROTOCOL_VERSION:
        raise ValueError(f"Unsupported protocol version {version}")

    payload = packet[HEADER_SIZE:payload_end]
    decoded_fields = decode_payload(msg_type, field_mask, payload)

    return {
        "version": version,
        "msg_type": MsgType(msg_type),
        "event_type": EventType(event_type),
        "device_id": device_id,
        "seq": seq,
        "timestamp": timestamp,
        "field_mask": field_mask,
        "fields": decoded_fields,
    }


def decode_payload(msg_type: int, field_mask: int, payload: bytes) -> dict:
    if msg_type != MsgType.BMV:
        raise ValueError(f"Unsupported msg_type {msg_type}")

    fields = {}
    offset = 0
    for bit, field_name, fmt in BMV_FIELD_LAYOUT:
        if not field_mask & int(bit):
            continue
        size = struct.calcsize(fmt)
        if offset + size > len(payload):
            raise ValueError("Payload shorter than field mask indicates")
        fields[field_name] = struct.unpack(fmt, payload[offset:offset + size])[0]
        offset += size

    if offset != len(payload):
        raise ValueError("Payload has unexpected trailing bytes")

    return fields
