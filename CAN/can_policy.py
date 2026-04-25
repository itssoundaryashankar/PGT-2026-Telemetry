#!/usr/bin/env python3
"""Generic delta + heartbeat transmit policy.

Modeled on BMVTransmitPolicy but parameterized: the caller provides a
mapping of field-name -> minimum-delta. Any field changing by more than
its delta triggers a transmit. A heartbeat ensures we send something
even when nothing has changed.

Returns an `event_type` enum-ish value when a transmit is warranted, or
None when not. The actual enum members come from the telemetry_packet
module — this policy just hands back whatever was passed in as
event_type_change / event_type_heartbeat at construction time.
"""

import time


class GenericTransmitPolicy:
    def __init__(self, deltas: dict, event_type_change, event_type_heartbeat,
                 heartbeat_seconds: float = 60.0):
        """
        deltas: {field_name: min_change_to_trigger_transmit}
        event_type_change: enum value to use when a delta is exceeded
        event_type_heartbeat: enum value to use for heartbeat-only sends
        heartbeat_seconds: send a heartbeat at least this often
        """
        self.deltas = dict(deltas)
        self.change_event = event_type_change
        self.heartbeat_event = event_type_heartbeat
        self.heartbeat_seconds = heartbeat_seconds

        self._last_sent_fields: dict = {}
        self._last_sent_time: float = 0.0
        self._seq: int = 0

    def classify(self, reading):
        fields = reading.get("fields", {})
        now = time.time()

        # First reading? Always send.
        if not self._last_sent_fields:
            return self.change_event

        # Heartbeat overdue?
        if now - self._last_sent_time >= self.heartbeat_seconds:
            return self.heartbeat_event

        # Any tracked field exceeded its delta?
        for name, threshold in self.deltas.items():
            if name not in fields:
                continue
            prev = self._last_sent_fields.get(name)
            if prev is None:
                return self.change_event
            if abs(fields[name] - prev) >= threshold:
                return self.change_event

        return None

    def mark_sent(self, reading):
        self._last_sent_fields = dict(reading.get("fields", {}))
        self._last_sent_time = time.time()
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq
