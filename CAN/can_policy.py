#!/usr/bin/env python3
"""Per-CAN-ID transmit policy.

Solar car CAN buses repeat the same frames at 10-100 Hz when nothing is
moving. LoRa cannot carry that. This policy classifies each frame into
one of:

    SAMPLE             — first time we've seen this ID
    DELTA_UPDATE       — payload bytes changed
    HEARTBEAT          — payload unchanged but >heartbeat_seconds since
                          last transmission for this ID
    None               — skip (rate-limited or duplicate)

State is tracked per CAN ID, so a fast-moving MPPT current frame and a
slow BMS state-of-charge frame each get their own cadence.
"""

import time

from telemetry_packet import EventType


class CANTransmitPolicy:
    def __init__(self, min_interval_seconds=1.0, heartbeat_seconds=60.0):
        """
        min_interval_seconds:  minimum gap between transmissions for the
                               same CAN ID, even when the payload changes.
                               Set to 0 to forward every change.
        heartbeat_seconds:     forward an unchanged frame after this long
                               of silence, so the receiver knows the bus
                               is still alive.
        """
        self.min_interval_seconds = min_interval_seconds
        self.heartbeat_seconds = heartbeat_seconds
        # Per-ID state: {can_id: {"data": bytes, "sent_at": float}}
        self._last_by_id = {}
        # Pending classification state for mark_sent to commit.
        self._pending = None
        self.seq = 0

    def classify(self, reading):
        can_id = reading["can_id"]
        data_hex = reading["fields"]["data_hex"]
        data = bytes.fromhex(data_hex) if data_hex else b""
        now = time.time()

        previous = self._last_by_id.get(can_id)
        event_type = None

        if previous is None:
            event_type = EventType.SAMPLE
        else:
            since_last = now - previous["sent_at"]
            changed = data != previous["data"]

            if changed and since_last >= self.min_interval_seconds:
                event_type = EventType.DELTA_UPDATE
            elif since_last >= self.heartbeat_seconds:
                event_type = EventType.HEARTBEAT
            # else: skip (rate-limited duplicate or rate-limited change)

        if event_type is not None:
            self._pending = (can_id, data, now)
        return event_type

    def mark_sent(self, reading):
        """Commit the most recent classify() decision and return seq."""
        if self._pending is not None:
            can_id, data, now = self._pending
            self._last_by_id[can_id] = {"data": data, "sent_at": now}
            self._pending = None

        current_seq = self.seq
        self.seq = (self.seq + 1) & 0xFFFF
        return current_seq
